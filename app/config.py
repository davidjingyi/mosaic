"""Configuration management using pydantic-settings.

Priority (high to low):
1. Environment variables (ONCO_* prefix)
2. .env file
3. data/config.yaml
4. Default values in this file
"""
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChunkingConfig(BaseSettings):
    chunk_size: int = 800
    chunk_overlap: int = 150
    separator: str = "\n\n"
    enable_contextual_chunking: bool = True
    contextual_prefix_format: str = (
        "[文档: {doc_title}][分类: {path_tags}][章节: {section}][位置: {position}/{total}]\n{content}"
    )


class DocTypeRule(BaseSettings):
    keywords: list[str] = []
    chunk_size: int = 500
    chunk_overlap: int = 50


class EmbeddingConfig(BaseSettings):
    model_name: str = "BAAI/bge-large-zh-v1.5"
    device: str = "cpu"
    normalize_embeddings: bool = True
    remote_url: str = ""  # e.g. http://127.0.0.1:8003 — when set, use remote embedder instead of local


class RetrievalConfig(BaseSettings):
    search_k: int = 15
    score_threshold: float = 0.15
    use_rerank: bool = True
    rerank_model: str = "BAAI/bge-reranker-large"
    rerank_top_k: int = 5


class HybridSearchConfig(BaseSettings):
    enabled: bool = True
    bm25_weight: float = Field(0.4, ge=0.0, le=1.0)
    vector_weight: float = Field(0.6, ge=0.0, le=1.0)
    rrf_k: int = Field(60, ge=1)


class QueryExpansionConfig(BaseSettings):
    enabled: bool = True
    synonym_dict: dict[str, list[str]] = Field(default_factory=dict)


class LLMConfig(BaseSettings):
    base_url: str = "https://api.moonshot.cn/v1"
    api_key: str = ""
    api_id: str = ""
    model_name: str = "moonshot-v1-8k"
    temperature: float = Field(0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(4096, ge=1)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    system_prompt: str = (
        "你是一位资深的肿瘤专科医生助手，拥有丰富的肿瘤学知识和临床经验。"
        "你的职责是为肿瘤患者及其家属提供专业、准确、温暖且易于理解的医疗咨询服务。\n\n"
        "回答原则：\n"
        "1. 基于提供的医学文献和资料进行回答，确保信息有据可依\n"
        "2. 使用通俗易懂的语言解释专业医学术语\n"
        "3. 保持温暖、关怀、耐心的态度\n"
        "4. 如果症状描述不够清晰，请礼貌地追问关键细节\n"
        "5. 对于超出资料范围的问题，坦诚告知并建议咨询主治医师\n"
        "6. 强调任何建议都不能替代主治医师的专业判断"
    )


class CancerBoostingConfig(BaseSettings):
    """癌种关键词加权配置 — 检索后按癌种匹配度重排序。"""
    enabled: bool = True
    boost_multiplier: float = Field(2.0, ge=1.0, le=10.0)
    """文档元数据匹配癌种时的分数倍率。1.0=不加权, 2.0=翻倍。"""
    cancer_keywords: dict[str, list[str]] = Field(default_factory=lambda: {
        "非小细胞肺癌": ["非小细胞肺癌", "NSCLC", "肺腺癌", "肺鳞癌", "肺癌"],
        "小细胞肺癌": ["小细胞肺癌", "SCLC"],
        "乳腺癌": ["乳腺癌", "breast cancer", "HER2阳性", "三阴性乳腺癌", "Luminal"],
        "结直肠癌": ["结直肠癌", "结肠癌", "直肠癌", "肠癌", "colorectal", "CRC", "大肠癌"],
        "胰腺癌": ["胰腺癌", "pancreatic"],
        "甲状腺癌": ["甲状腺癌", "甲状腺", "thyroid"],
        "肝癌": ["肝癌", "肝细胞癌", "HCC", "hepatocellular"],
        "胃癌": ["胃癌", "gastric"],
        "食管癌": ["食管癌", "食管", "esophageal", "食道癌"],
        "肾癌": ["肾癌", "renal", "RCC"],
        "前列腺癌": ["前列腺癌", "prostate"],
        "卵巢癌": ["卵巢癌", "ovarian"],
        "宫颈癌": ["宫颈癌", "cervical", "子宫颈癌"],
        "淋巴瘤": ["淋巴瘤", "lymphoma", "霍奇金", "非霍奇金"],
        "黑色素瘤": ["黑色素瘤", "melanoma"],
        "骨与软组织肿瘤": ["骨肿瘤", "软组织", "肉瘤", "sarcoma"],
        "胆道恶性肿瘤": ["胆道", "胆管癌", "胆囊癌", "cholangiocarcinoma"],
        "头颈部肿瘤": ["头颈部", "鼻咽癌", "喉癌", "口腔癌", "NPC"],
        "骨髓增殖性肿瘤": ["骨髓增殖", "MPN", "骨髓纤维化", "真性红细胞增多症", "原发性血小板增多症"],
    })


class KnowledgeBaseConfig(BaseSettings):
    pdf_paths: list[str] = Field(default_factory=lambda: ["./knowledge-base/guidelines"])
    markdown_paths: list[str] = Field(default_factory=lambda: ["./knowledge-base/patient-edu"])
    auto_scan: bool = True
    scan_interval_hours: int = 24


class CacheConfig(BaseSettings):
    enabled: bool = False
    max_size: int = Field(1000, ge=100, le=10000)
    similarity_threshold: float = Field(0.92, ge=0.5, le=1.0)
    default_ttl: int = Field(3600, ge=60)
    medical_fact_ttl: int = Field(86400, ge=3600)
    guideline_ttl: int = Field(43200, ge=1800)
    persist_path: str = "data/query_cache.json"
    save_interval: int = Field(300, ge=60)


class TerminologyConfig(BaseSettings):
    enabled: bool = True
    source: str = "medct"  # medct | builtin | none
    medct_path: str = "data/medct"
    medct_download_url: str = (
        "https://huggingface.co/datasets/TigerResearch/MedCT/resolve/main"
    )
    auto_download: bool = True
    custom_rules: dict[str, list[str]] = Field(default_factory=dict)


class Settings(BaseSettings):
    """Root settings model."""

    model_config = SettingsConfigDict(
        env_prefix="ONCO_",
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env",
    )

    # Chunking
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    doc_type_rules: list[DocTypeRule] = Field(
        default_factory=lambda: [
            DocTypeRule(
                keywords=["指南", "guideline", "共识", "CSCO", "NCCN", "专家共识"],
                chunk_size=1500,
                chunk_overlap=300,
            ),
            DocTypeRule(
                keywords=["试验", "trial", "临床", "研究", "clinical"],
                chunk_size=600,
                chunk_overlap=100,
            ),
            DocTypeRule(
                keywords=["科普", "患教", "问答", "faq", "知识"],
                chunk_size=400,
                chunk_overlap=80,
            ),
        ]
    )

    # Components
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    hybrid_search: HybridSearchConfig = Field(default_factory=HybridSearchConfig)
    cancer_boosting: CancerBoostingConfig = Field(default_factory=CancerBoostingConfig)
    query_expansion: QueryExpansionConfig = Field(
        default_factory=lambda: QueryExpansionConfig(
            synonym_dict={
                "肺癌": ["NSCLC", "非小细胞肺癌", "SCLC", "小细胞肺癌", "lung cancer"],
                "胃癌": ["gastric cancer", "stomach cancer"],
                "肠癌": ["colorectal cancer", "CRC", "结直肠癌", "大肠癌"],
                "肝癌": ["HCC", "肝细胞癌", "hepatocellular carcinoma"],
                "乳腺癌": ["breast cancer"],
                "食管癌": ["esophageal cancer", "食道癌"],
                "鼻咽癌": ["nasopharyngeal carcinoma", "NPC"],
                "免疫治疗": ["PD-1", "PD-L1", "免疫检查点抑制剂", "ICI"],
                "化疗": ["化学治疗", "chemical therapy"],
                "靶向": ["targeted therapy", "分子靶向", "靶向治疗"],
                "放疗": ["放射治疗", "radiation therapy"],
                "副作用": ["不良反应", "AE", "adverse event"],
                "生存期": ["OS", "总生存期", "PFS", "无进展生存期"],
                "分期": ["TNM", "I期", "II期", "III期", "IV期", "早期", "晚期"],
                "标志物": ["biomarker", "生物标志物", "MSI", "TMB"],
            }
        )
    )
    terminology: TerminologyConfig = Field(default_factory=TerminologyConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    data_dir: Path = Path("./data")
    knowledge_base_dir: Path = Path("./knowledge-base")

    # 密码派生主密钥 — 用于将用户密码派生为付费激活码
    derive_master_key: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        """Load settings from YAML file without env override.

        Uses model_validate() so that YAML values take precedence over
        defaults and are NOT overridden by .env / environment variables.
        This ensures that admin UI saved config is authoritative.
        """
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        """Save non-default settings to YAML."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()
        # Convert Path objects to strings for YAML serialization
        def _convert_paths(obj: Any) -> Any:
            if isinstance(obj, Path):
                return str(obj)
            if isinstance(obj, dict):
                return {k: _convert_paths(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_convert_paths(v) for v in obj]
            return obj
        data = _convert_paths(data)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
