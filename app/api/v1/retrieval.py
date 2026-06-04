"""Retrieval debug API routes."""
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import get_engine, require_admin
from app.core.engine import RAGEngine
from app.services import prompt_store

router = APIRouter(prefix="/retrieve", tags=["检索调试"])


class RetrieveRequest(BaseModel):
    query: str = Field(...)
    k: int | None = Field(200, description="返回结果数量")
    show_details: bool = Field(True)
    show_prompt: bool = Field(True, description="是否返回构建的 prompt")


@router.post("")
async def retrieve_debug(request: Request, body: RetrieveRequest, admin=Depends(require_admin)):
    engine: RAGEngine = get_engine(request)
    retriever = engine.retriever

    expanded_query = ""
    if hasattr(retriever, "expander") and retriever.expander:
        expanded_query = retriever.expander.expand(body.query)

    # ── Determine effective search_k (same as retriever's internal logic) ──
    detected_types = retriever.booster.detect_cancer_types(body.query) if hasattr(retriever, "booster") and retriever.booster else []
    base_k = body.k or 50
    effective_k = base_k * 4 if detected_types else base_k

    # Save original config and override for deep retrieval
    original_search_k = retriever.retrieval_config.search_k
    try:
        retriever.retrieval_config.search_k = base_k
        final_docs = engine.retriever.retrieve(body.query)
    finally:
        retriever.retrieval_config.search_k = original_search_k

    # ── BM25 results (standalone, match retriever's internal k) ──
    bm25_results: list[dict[str, Any]] = []
    if hasattr(retriever, "_bm25_search") and retriever._bm25 is not None:
        bm25_raw = retriever._bm25_search(expanded_query or body.query, k=effective_k)
        for doc, score in bm25_raw:
            bm25_results.append({
                "doc_id": doc.metadata.get("doc_id", ""),
                "chunk_index": doc.metadata.get("chunk_index", 0),
                "bm25_score": round(score, 4),
                "preview": doc.page_content[:200],
            })

    # ── Vector results (standalone, match retriever's internal k) ──
    vector_results: list[dict[str, Any]] = []
    if hasattr(retriever, "_vector_search"):
        vector_raw = retriever._vector_search(expanded_query or body.query, k=effective_k)
        for doc, score in vector_raw:
            vector_results.append({
                "doc_id": doc.metadata.get("doc_id", ""),
                "chunk_index": doc.metadata.get("chunk_index", 0),
                "vector_score": round(score, 4),
                "preview": doc.page_content[:200],
            })

    # ── Build BM25 & Vector lookup maps by (doc_id, chunk_index) ──
    bm25_map: dict[tuple, float] = {}
    for r in bm25_results:
        key = (r["doc_id"], r["chunk_index"])
        if key not in bm25_map:
            bm25_map[key] = r["bm25_score"]

    vector_map: dict[tuple, float] = {}
    for r in vector_results:
        key = (r["doc_id"], r["chunk_index"])
        if key not in vector_map:
            vector_map[key] = r["vector_score"]

    # ── Final ranked results (top k) with cross-referenced scores ──
    final_results = []
    max_rank = min(base_k, len(final_docs))
    for rank, doc in enumerate(final_docs[:max_rank], 1):
        did = doc.metadata.get("doc_id", "")
        ci = doc.metadata.get("chunk_index", 0)
        key = (did, ci)
        final_results.append({
            "rank": rank,
            "doc_id": did,
            "chunk_index": ci,
            "source": doc.metadata.get("source", ""),
            "document_title": doc.metadata.get("document_title", "") or doc.metadata.get("source", ""),
            "bm25_score": round(bm25_map.get(key, 0), 4),
            "vector_score": round(vector_map.get(key, 0), 4),
            "rrf_score": round(doc.metadata.get("rrf_score", 0), 4),
            "rerank_score": round(doc.metadata.get("rerank_score", 0), 4),
            "final_score": round(doc.metadata.get("score", 0), 4),
            "preview": doc.page_content[:300],
        })

    # ── Build the prompt that would be sent to LLM ──
    prompt_info = None
    if body.show_prompt:
        default_prompt = prompt_store.get_default_prompt()
        system_prompt = default_prompt["content"] if default_prompt else ""
        # Only use top K contexts in prompt (same as what retriever actually uses)
        top_k_docs = final_docs[:base_k]
        contexts = [doc.page_content for doc in top_k_docs]
        context_text = "\n\n---\n\n".join(
            f"[段落 {i + 1}]\n{ctx}" for i, ctx in enumerate(contexts)
        )
        user_message = (
            f"根据以下上下文信息回答问题：\n\n{context_text}"
            f"\n\n---\n\n问题：{body.query}\n\n请基于上述上下文回答。"
        )
        prompt_info = {
            "system_prompt": system_prompt,
            "system_prompt_length": len(system_prompt),
            "user_message": user_message,
            "user_message_length": len(user_message),
            "full_prompt": f"[System]\n{system_prompt}\n\n[User]\n{user_message}",
            "total_tokens_estimate": len(system_prompt + user_message) // 2,
            "context_chunks": len(contexts),
        }

    return {
        "query": body.query,
        "expanded_query": expanded_query,
        "detected_cancer_types": detected_types,
        "effective_search_k": effective_k,
        "bm25_results_count": len(bm25_results),
        "vector_results_count": len(vector_results),
        "final_results_count": len(final_results),
        "bm25_results": bm25_results,
        "vector_results": vector_results,
        "final_results": final_results,
        "prompt": prompt_info,
    }
