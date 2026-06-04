# Mosaic
  Mosaic 项目简介

  Mosaic 是一个模块化的 RAG（检索增强生成）智能问答框架，当前以"肿瘤患者智能答疑系统"为应用场景，但架构上完全领域无关——
  换文档和词典就能切到法律、金融、教育等任何垂直行业。

  核心理念

  名字 "Mosaic"（马赛克）的含义：知识如碎片，检索拼成答案。每个组件（嵌入器、检索器、分词器、Query增强器 ）都是可插拔的马
  赛克瓷砖，按需自由组合。

  检索流水线（核心技术亮点）

  用户提问
    →🔬 癌种检测 + Query增强（关键词重复4遍主导向量方向）
    →🔍 混合检索：BM25关键词 + Dense向量 →RRF融合算法
    →📈 癌种加权：匹配文档分数 ×N倍乘数，重新排序
    →🎯 Cross-Encoder 精排（可选）
    →💬 LLM（DeepSeek）流式生成答案

  这里面有几个精巧的设计：

  1. 癌种增强器 (cancer_booster.py)：用户问"乳腺癌HER2方案"时，会自动在query前追加4遍"乳腺癌"，让这个词在embedding向量中
  占据主导权重；检索后还会对命中文档做分数加权重排
  2. 混合检索 + RRF融合：BM25擅长精确关键词匹配，Dense向量擅长语义理解，两者结果通过RRF（倒数排名融合）合并，互补长短
  3. 语义缓存：用余弦相似度判断两个问题是否"问的同一件事"，命中缓存直接返回，亚秒级延迟
  4. 领域词典驱动：jieba分词预加载了30+医学术语；同义词词典支持中英文双向扩展

 
  一句话总结

  ▎ 一个架构优雅、组件可插拔的 RAG 问答引擎，用"癌种检测增强 + BM25/Dense混合检索 + RRF融合 +
  ▎ 语义缓存"四板斧，把肿瘤领域的文档检索做到了精准可控——换套文档和词典就是一个全新的领域问答系统。
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
