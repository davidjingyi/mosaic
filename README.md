# Mosaic

> 知识如碎片，检索拼成答案。
> Knowledge is a mosaic — retrieve the pieces, assemble the answer.

**English** | [中文](#中文)

---

**Mosaic** is a modular RAG (Retrieval-Augmented Generation) framework. It breaks document collections into knowledge fragments, stitches them into precise context through multi-path retrieval, and hands them to an LLM for grounded answers.

Not tied to any industry — swap the documents and domain dictionary, and you're in healthcare, law, finance, education, or any vertical.

## Why "Mosaic"?

A mosaic is made of independent tiles, each replaceable. Mosaic's components — embedder, retriever, tokenizer, query enhancer — are all pluggable modules. Compose the pipeline you need.

## Architecture

```
User Query
    │
    ▼
┌──────────────┐
│  Query Boost  │  Domain dictionary → keyword detection → query expansion
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Hybrid       │  BM25 + Dense Vector → RRF Fusion
│  Retrieval    │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Score Boost  │  Domain-matched chunks × N multiplier
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Rerank       │  Cross-encoder fine-ranking
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  LLM Generate │  Retrieved context + system prompt → answer
└──────────────┘
```

## Features

- 🔍 **Multi-path hybrid retrieval** — BM25 keywords + dense vectors → RRF fusion → cross-encoder rerank
- 🧩 **Domain-aware boosting** — configurable keyword dictionaries, pre-retrieval query expansion + post-retrieval score weighting
- 📚 **Full document lifecycle** — PDF upload → smart chunking → vector ingestion, one click
- ⚡ **Semantic cache** — similar queries hit cache, sub-second latency
- 💬 **Multi-turn conversations** — session management, hot-swappable model configs
- 🛡️ **Auth system** — activation-code registration, bcrypt hashing, JWT
- 🌐 **Admin dashboard** — retrieval debug panel (Top200 rankings + 4 scoring dimensions + full prompt), PDF source mapping

## Stack

| Layer | Tech |
|-------|------|
| Web | FastAPI + Uvicorn + Gunicorn |
| Vector DB | ChromaDB |
| Embedding | BAAI/bge-m3 |
| Reranker | BAAI/bge-reranker-large |
| Tokenizer | jieba |
| LLM | DeepSeek / OpenAI-compatible API |
| Frontend | Vanilla HTML/CSS/JS |

## Quick Start

```bash
git clone https://github.com/your-username/mosaic.git
cd mosaic
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit: set llm.api_key
python start_embedding.py &           # embedding service (separate process)
python start_app.py                   # or: gunicorn -c gunicorn_conf.py app.main:app
```

- User UI: `http://localhost:8000`
- Admin panel: `http://localhost:8000/admin/`
- API docs: `http://localhost:8000/docs`

## Domain Switching

Mosaic's core modules are domain-agnostic. To switch domains:

1. **Swap docs** — drop target-domain PDFs into `knowledge-base/`
2. **Swap dictionary** — replace terms in `query_expansion.synonym_dict`:

```yaml
# Oncology
query_expansion:
  synonym_dict:
    immunotherapy: [PD-1, PD-L1, ICI]

# → Law
query_expansion:
  synonym_dict:
    contract_dispute: [breach, damages, contract law]
```

## Project Structure

```
mosaic/
├── app/
│   ├── api/v1/          # REST endpoints
│   ├── core/            # Pluggable engine
│   │   ├── retriever    # BM25 + Vector + RRF
│   │   ├── embedder     # Local / remote embedding
│   │   ├── chunker      # Document chunking
│   │   ├── generator    # LLM generation
│   │   ├── reranker     # Cross-encoder rerank
│   │   ├── query_expander    # Query expansion
│   │   ├── cancer_booster    # Domain keyword booster (generalizable)
│   │   └── semantic_cache    # Semantic dedup cache
│   ├── services/        # Business logic
│   ├── models/          # Pydantic schemas
│   └── static/          # Frontend
├── tests/
├── config.example.yaml
├── embedding_service.py
└── start_app.py
```

## License

MIT

---

## 中文

**Mosaic** 是一个模块化的 RAG（检索增强生成）框架。将任意文档集切分为知识碎片，通过多路检索拼接成精准上下文，交给 LLM 生成可靠答案。不绑定行业——换文档、换词典即可切到任何垂直领域。

### 为什么叫 Mosaic？

马赛克由独立碎片拼接而成，每块可替换。Mosaic 的嵌入器、检索器、分词器、Query 增强器都是可插拔模块，按需组合检索流水线。

### 领域切换

1. **换文档**：将目标领域 PDF 放入 `knowledge-base/`
2. **换词典**：修改 `query_expansion.synonym_dict`，替换为领域术语
