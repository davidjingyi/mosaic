# Mosaic

> 知识如碎片，检索拼成答案。  
> Knowledge is a mosaic — retrieve the pieces, assemble the answer.

[**中文**](#中文) | [**English**](#english)

---

## 中文

**Mosaic** 是一个模块化的 RAG（检索增强生成）智能问答框架，以"肿瘤患者智能答疑系统"为初始应用场景，但架构上完全领域无关——换文档、换词典，就能切到法律、金融、教育等任何垂直行业。

### 为什么叫 Mosaic？

马赛克由独立的碎片拼接而成，每一块都可以替换。Mosaic 的每个组件——嵌入器、检索器、分词器、Query 增强器——都是可插拔的模块，按需自由组合你的检索流水线。

### 检索流水线

```
用户提问
  → 🔬 癌种检测 + Query 增强（关键词重复 4 遍，主导向量方向）
  → 🔍 混合检索：BM25 关键词 + Dense 向量 → RRF 融合
  → 📈 癌种加权：匹配文档分数 × 倍乘数，重新排序
  → 🎯 Cross-Encoder 精排（可选）
  → 💬 LLM 流式生成答案
```

### 核心设计

**癌种增强器** (`cancer_booster.py`) — 用户问"乳腺癌 HER2 方案"时，自动在 query 前追加 4 遍"乳腺癌"，让这个词在 embedding 向量中占据主导权重；检索后再对命中文档做分数加权重排，确保目标癌种文档优先。

**混合检索 + RRF 融合** — BM25 擅长精确关键词匹配，Dense 向量擅长语义理解。两者结果通过 RRF（倒数排名融合）合并，互补长短。

**语义缓存** — 用余弦相似度判断两个问题是否"问的同一件事"。命中缓存直接返回，亚秒级延迟；支持 LRU + TTL 双驱逐策略，磁盘持久化。

**领域词典驱动** — jieba 分词预加载 30+ 医学术语；同义词词典支持中英文双向扩展，query 端自动扩充检索词。

### 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Gunicorn + Uvicorn |
| 向量库 | ChromaDB |
| Embedding | BAAI/bge-m3 |
| 精排 | BAAI/bge-reranker-large |
| 分词 | jieba |
| LLM | DeepSeek / 兼容 OpenAI 接口 |
| 前端 | 原生 HTML/CSS/JS（用户端 + 管理后台） |
| 认证 | bcrypt + JWT |

### 项目结构

```
mosaic/
├── app/
│   ├── api/v1/              # 14 个 REST 端点
│   ├── core/                # 可插拔引擎核心
│   │   ├── retriever.py     # BM25 + Vector + RRF 混合检索
│   │   ├── cancer_booster.py # 领域关键词加权重排
│   │   ├── embedder.py      # 本地 / 远程向量化
│   │   ├── chunker.py       # 智能文档切块（上下文前缀）
│   │   ├── reranker.py      # Cross-Encoder 精排
│   │   ├── query_expander.py # 同义词扩展
│   │   ├── semantic_cache.py # 语义去重缓存（LRU + TTL）
│   │   └── generator.py     # LLM 流式生成
│   ├── services/            # 业务逻辑层
│   ├── models/              # Pydantic Schema
│   └── static/              # 前端（用户 UI + Admin 面板）
├── tests/                   # 测试套件
├── config.example.yaml      # 配置模板
├── deploy.sh / deploy.bat   # 一键部署
└── requirements.txt
```

### 快速开始

```bash
git clone https://github.com/davidjingyi/mosaic.git
cd mosaic
pip install -r requirements.txt
cp config.example.yaml config.yaml   # 编辑：设置 llm.api_key
python start_embedding.py &           # embedding 服务（独立进程）
python start_app.py                   # 或: gunicorn -c gunicorn_conf.py app.main:app
```

- 用户界面：`http://localhost:8000`
- 管理后台：`http://localhost:8000/admin/`
- API 文档：`http://localhost:8000/docs`

### 领域切换

1. **换文档**：将目标领域 PDF 放入 `knowledge-base/`
2. **换词典**：修改 `config.yaml` 中的 `query_expansion.synonym_dict`：

```yaml
# 肿瘤 → 法律
query_expansion:
  synonym_dict:
    contract_dispute: [breach, damages, contract law]
    知识产权: [IP, patent, trademark, copyright]
```

---

## English

**Mosaic** is a modular RAG (Retrieval-Augmented Generation) framework. It breaks document collections into knowledge fragments, stitches them into precise context through multi-path retrieval, and hands them to an LLM for grounded answers.

Not tied to any industry — swap the documents and domain dictionary, and you're in healthcare, law, finance, education, or any vertical.

### Architecture

```
User Query
    │
    ▼
┌──────────────────┐
│  Query Boost     │  Domain dictionary → keyword detection → expansion
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Hybrid          │  BM25 + Dense Vector → RRF Fusion
│  Retrieval       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Score Boost     │  Domain-matched chunks × N multiplier
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Rerank          │  Cross-encoder fine-ranking
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  LLM Generate    │  Retrieved context + prompt → answer
└──────────────────┘
```

### Features

- 🔍 **Multi-path hybrid retrieval** — BM25 keywords + dense vectors → RRF fusion → cross-encoder rerank
- 🧩 **Domain-aware boosting** — configurable keyword dictionaries, pre-retrieval query expansion + post-retrieval score weighting
- 📚 **Full document lifecycle** — PDF upload → smart chunking → vector ingestion, one click
- ⚡ **Semantic cache** — similar queries hit cache, sub-second latency (cosine similarity + LRU + TTL)
- 💬 **Multi-turn conversations** — session management, hot-swappable model configs
- 🛡️ **Auth system** — activation-code registration, bcrypt hashing, JWT
- 🌐 **Admin dashboard** — retrieval debug panel (Top200 rankings + 4 scoring dimensions + full prompt), PDF source mapping

### Stack

| Layer | Tech |
|-------|------|
| Web | FastAPI + Uvicorn + Gunicorn |
| Vector DB | ChromaDB |
| Embedding | BAAI/bge-m3 |
| Reranker | BAAI/bge-reranker-large |
| Tokenizer | jieba |
| LLM | DeepSeek / OpenAI-compatible API |
| Frontend | Vanilla HTML/CSS/JS |

### Quick Start

```bash
git clone https://github.com/davidjingyi/mosaic.git
cd mosaic
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit: set llm.api_key
python start_embedding.py &           # embedding service (separate process)
python start_app.py                   # or: gunicorn -c gunicorn_conf.py app.main:app
```

- User UI: `http://localhost:8000`
- Admin panel: `http://localhost:8000/admin/`
- API docs: `http://localhost:8000/docs`

### Domain Switching

Mosaic's core modules are domain-agnostic. To switch domains:

1. **Swap docs** — drop target-domain PDFs into `knowledge-base/`
2. **Swap dictionary** — replace terms in `query_expansion.synonym_dict`:

```yaml
# Oncology → Law
query_expansion:
  synonym_dict:
    contract_dispute: [breach, damages, contract law]
    intellectual_property: [IP, patent, trademark, copyright]
```

## License

MIT
