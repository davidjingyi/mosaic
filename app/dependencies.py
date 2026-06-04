"""FastAPI dependency injection and component lifecycle management."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from app.config import Settings
from app.core.chunker import DefaultChunker
from app.core.document_parser import DefaultDocumentParser
from app.core.embedder import STEmbedder
from app.core.remote_embedder import RemoteEmbedder, EMBEDDING_SERVICE_URL
from app.core.engine import RAGEngine
from app.core.generator import OpenAIGenerator
from app.core.query_expander import MedicalQueryExpander
from app.core.reranker import BGEReranker
from app.core.retriever import HybridRetriever
from app.core.auth import init_admin_auth
from app.core.query_expander import MEDCT_WARNINGS
from app.core.semantic_cache import DefaultSemanticCache
from app.services import prompt_store
from app.core.vector_store import ChromaVectorStore
from app.services.ingest_job_manager import IngestJobManager
from app.services.model_store import ModelStore
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/config.yaml")


def _load_settings() -> Settings:
    settings = Settings.from_yaml(_CONFIG_PATH) if _CONFIG_PATH.exists() else Settings()
    # Apply active model config from model_store to ensure LLM config is never stale
    try:
        from app.services.model_store import ModelStore
        ms = ModelStore(settings.data_dir)
        active_llm = ms.get_active_llm_config()
        if active_llm:
            llm_config = settings.llm.model_dump()
            # Defensive strip on all string values
            clean = {k: v.strip() if isinstance(v, str) else v for k, v in active_llm.items()}
            # Only override if model_store has a real value (prevent empty strings overwriting defaults)
            for k, v in clean.items():
                if isinstance(v, str) and not v:
                    continue  # skip empty strings from model_store
                llm_config[k] = v
            from app.config import LLMConfig
            settings.llm = LLMConfig(**llm_config)
    except Exception:
        pass
    return settings


def _save_settings(settings: Settings) -> None:
    settings.to_yaml(_CONFIG_PATH)


def _create_engine(settings: Settings) -> RAGEngine:
    parser = DefaultDocumentParser()
    chunker = DefaultChunker(settings.chunking, settings.doc_type_rules)

    # Remote embedding: use shared service to avoid loading model per worker
    if settings.embedding.remote_url:
        from app.core.remote_embedder import RemoteEmbedder
        embedder = RemoteEmbedder(service_url=settings.embedding.remote_url)
        logger.info("Using remote embedding service: %s", settings.embedding.remote_url)
    else:
        embedder = STEmbedder(settings.embedding)
        logger.info("Using local embedding model: %s on %s", settings.embedding.model_name, settings.embedding.device)
    vector_store = ChromaVectorStore(str(settings.data_dir / "vector_db"), embedder)
    retriever = HybridRetriever(
        vector_store,
        embedder,
        settings.retrieval,
        settings.hybrid_search,
        booster_config=settings.cancer_boosting,
        expander=MedicalQueryExpander(settings.terminology) if settings.terminology.enabled else None,
    )
    reranker = BGEReranker(settings.retrieval) if settings.retrieval.use_rerank else None
    generator = OpenAIGenerator(settings.llm)
    cache = DefaultSemanticCache(settings.cache, embedder=embedder)

    return RAGEngine(
        settings=settings,
        parser=parser,
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
        retriever=retriever,
        reranker=reranker,
        expander=retriever.expander,
        generator=generator,
        cache=cache,
    )


class ComponentManager:
    """Manages RAG components and supports hot-reloading."""

    def __init__(self, app: FastAPI):
        self.app = app
        self.settings = _load_settings()
        self.engine = _create_engine(self.settings)
        self.session_store = SessionStore(self.settings.data_dir)

    async def reload_config(self, new_settings: Settings) -> None:
        """Hot-reload components affected by config changes."""
        old = self.settings
        self.settings = new_settings
        # Sync app.state so GET /config returns the latest values
        self.app.state.settings = new_settings
        _save_settings(new_settings)

        # Reload chunker if chunking config changed
        if new_settings.chunking != old.chunking or new_settings.doc_type_rules != old.doc_type_rules:
            self.engine.chunker = DefaultChunker(new_settings.chunking, new_settings.doc_type_rules)
            logger.info("Hot-reloaded chunker")

        # Reload embedder if embedding config changed
        if new_settings.embedding != old.embedding:
            self.engine.embedder.reload_model(new_settings.embedding)
            logger.info("Hot-reloaded embedder")

        # Reload retriever if retrieval/hybrid config changed
        if new_settings.retrieval != old.retrieval or new_settings.hybrid_search != old.hybrid_search:
            self.engine.retriever.update_config(new_settings.retrieval, new_settings.hybrid_search)
            logger.info("Hot-reloaded retriever")

        # Reload cancer booster if boosting config changed
        if new_settings.cancer_boosting != old.cancer_boosting:
            self.engine.retriever.update_booster(new_settings.cancer_boosting)
            logger.info("Hot-reloaded cancer booster")

        # Reload reranker
        if new_settings.retrieval.use_rerank != old.retrieval.use_rerank or \
           new_settings.retrieval.rerank_model != old.retrieval.rerank_model:
            self.engine.reranker = BGEReranker(new_settings.retrieval) if new_settings.retrieval.use_rerank else None
            logger.info("Hot-reloaded reranker")

        # Reload generator if LLM config changed
        if new_settings.llm != old.llm:
            await self.engine.generator.reload(new_settings.llm)
            logger.info("Hot-reloaded generator")

        # Reload expander if terminology config changed
        if new_settings.terminology != old.terminology:
            self.engine.expander = (
                MedicalQueryExpander(new_settings.terminology)
                if new_settings.terminology.enabled else None
            )
            if self.engine.retriever:
                self.engine.retriever.expander = self.engine.expander
            logger.info("Hot-reloaded expander")

        # Reload cache if cache config changed
        if new_settings.cache != old.cache:
            self.engine.cache.stop()
            self.engine.cache = DefaultSemanticCache(new_settings.cache, embedder=self.engine.embedder)
            logger.info("Hot-reloaded cache")

    def get_stats(self) -> dict:
        """Return system statistics for admin dashboard."""
        from app.services.document_registry import get_documents

        cache_stats = self.engine.cache.get_stats()
        # Use document registry (source of truth) instead of raw vector store
        registry_docs = get_documents()
        indexed_docs = [d for d in registry_docs if d.get("status") in ("indexed", "embedding", "imported")]
        vector_count = self.engine.vector_store.count()
        sessions = self.session_store.list_all(limit=1000)
        jobs = self.app.state.job_manager.list_jobs(limit=50) if hasattr(self.app.state, 'job_manager') else []
        # Vector DB disk size
        db_path = Path("data/vector_db/chroma.sqlite3")
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0
        return {
            "version": "2.1.0",
            "documents": {
                "total": len(registry_docs),
                "indexed": len(indexed_docs),
                "pending": sum(1 for d in registry_docs if d.get("status") in ("pending", "failed", "scanned", "uploaded")),
                "chunked": sum(1 for d in registry_docs if d.get("status") == "chunked"),
                "total_chunks": vector_count,
            },
            "vector_store": {
                "db_size_mb": db_size_mb,
                "collection_name": getattr(self.engine.vector_store, '_collection', None) and getattr(self.engine.vector_store._collection, 'name', 'unknown') or 'unknown',
            },
            "jobs": {
                "total": len(jobs),
                "running": sum(1 for j in jobs if j.get("status") in ("running", "pending")),
                "completed": sum(1 for j in jobs if j.get("status") == "completed"),
                "failed": sum(1 for j in jobs if j.get("status") == "failed"),
                "cancelled": sum(1 for j in jobs if j.get("status") == "cancelled"),
            },
            "cache": cache_stats,
            "sessions": {
                "total": len(sessions),
                "recent": [s.to_dict() for s in sessions[:5]],
            },
            "config_summary": {
                "llm_model": self.settings.llm.model_name,
                "embedding_model": self.settings.embedding.model_name,
                "use_rerank": self.settings.retrieval.use_rerank,
                "hybrid_enabled": self.settings.hybrid_search.enabled,
            },
            "warnings": MEDCT_WARNINGS.copy(),
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    manager = ComponentManager(app)
    app.state.manager = manager
    app.state.settings = manager.settings
    app.state.engine = manager.engine
    app.state.session_store = manager.session_store
    app.state.job_manager = IngestJobManager()
    app.state.model_store = ModelStore(manager.settings.data_dir)

    logger.info("[Startup] Onco-RAG v2.0 initializing...")
    logger.info("[Startup] Vector store: %d chunks", manager.engine.vector_store.count())
    cache_stats = manager.engine.cache.get_stats()
    logger.info(
        "[Startup] Cache: %d entries, hit_rate=%.2f%%",
        cache_stats["current_size"],
        cache_stats["hit_rate"] * 100,
    )
    init_admin_auth()
    prompt_store.import_from_config(manager.settings.llm.system_prompt)

    # Clean up stale user accounts (inactive > 30 days)
    try:
        from app.core.user_auth import cleanup_stale_accounts
        removed = cleanup_stale_accounts(days=30)
        if removed:
            logger.info("[Startup] Cleaned up %d stale user accounts", removed)
    except Exception as e:
        logger.warning("[Startup] User cleanup failed: %s", e)

    # ------------------------------------------------------------------
    # Startup health check: fix orphaned "embedding" documents
    # ------------------------------------------------------------------
    try:
        from app.services import document_registry as _registry

        embedding_docs = _registry.get_documents(status="embedding")
        if embedding_docs:
            # Check which docs are actually being processed by a running job
            running_embed_jobs = [
                j for j in app.state.job_manager.list_jobs(limit=50, job_type="embed")
                if j.get("status") == "running"
            ]
            running_doc_ids = set()
            for job in running_embed_jobs:
                running_doc_ids.update(job.get("doc_ids", []))

            orphaned = [d for d in embedding_docs if d["doc_id"] not in running_doc_ids]
            if orphaned:
                logger.warning(
                    "[Startup] Found %d orphaned embedding documents (no running job). Auto-recovering to 'chunked'...",
                    len(orphaned),
                )
                for doc in orphaned:
                    _registry.update_document(
                        doc["doc_id"],
                        status="chunked",
                        error_msg="Auto-recovered: embedding interrupted (server restart / crash)",
                    )
                logger.info("[Startup] Auto-recovered %d documents to 'chunked'", len(orphaned))
    except Exception as e:
        logger.warning("[Startup] Failed to auto-recover embedding documents: %s", e)

    yield
    logger.info("[Shutdown] Persisting cache and sessions...")
    manager.engine.cache.stop()


def get_manager(request: Request) -> ComponentManager:
    return request.app.state.manager


def get_engine(request: Request) -> RAGEngine:
    return request.app.state.engine


def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
