"""Preset configuration profiles for quick parameter tuning."""

from app.config import Settings


# ---------------------------------------------------------------------------
# Preset profiles
# ---------------------------------------------------------------------------

_CONFIG_PROFILES = {
    "guideline-heavy": {
        "name": "指南共识方案",
        "description": "适合指南、共识、CSCO/NCCN 等长文档为主的知识库，chunk_size 较大以保留上下文",
        "config": {
            "chunking": {
                "chunk_size": 1200,
                "chunk_overlap": 250,
                "enable_contextual_chunking": True,
            },
            "doc_type_rules": [
                {
                    "keywords": ["指南", "guideline", "共识", "CSCO", "NCCN", "专家共识"],
                    "chunk_size": 1500,
                    "chunk_overlap": 300,
                },
                {
                    "keywords": ["试验", "trial", "临床", "研究", "clinical"],
                    "chunk_size": 800,
                    "chunk_overlap": 150,
                },
                {
                    "keywords": ["科普", "患教", "问答", "faq", "知识"],
                    "chunk_size": 500,
                    "chunk_overlap": 100,
                },
            ],
            "retrieval": {
                "search_k": 20,
                "rerank_top_k": 7,
                "score_threshold": 0.15,
            },
        },
    },
    "patient-edu": {
        "name": "科普问答方案",
        "description": "适合患教科普、FAQ 等短文档为主的知识库，chunk_size 较小，检索精度高",
        "config": {
            "chunking": {
                "chunk_size": 500,
                "chunk_overlap": 100,
                "enable_contextual_chunking": True,
            },
            "doc_type_rules": [
                {
                    "keywords": ["指南", "guideline", "共识", "CSCO", "NCCN", "专家共识"],
                    "chunk_size": 1000,
                    "chunk_overlap": 200,
                },
                {
                    "keywords": ["试验", "trial", "临床", "研究", "clinical"],
                    "chunk_size": 600,
                    "chunk_overlap": 100,
                },
                {
                    "keywords": ["科普", "患教", "问答", "faq", "知识"],
                    "chunk_size": 400,
                    "chunk_overlap": 80,
                },
            ],
            "retrieval": {
                "search_k": 10,
                "rerank_top_k": 3,
                "score_threshold": 0.2,
            },
        },
    },
    "precision": {
        "name": "精准检索方案",
        "description": "追求答案准确性，检索门槛高，混合检索+重排序全部开启",
        "config": {
            "retrieval": {
                "search_k": 25,
                "use_rerank": True,
                "rerank_top_k": 5,
                "score_threshold": 0.25,
            },
            "hybrid_search": {
                "enabled": True,
                "bm25_weight": 0.3,
                "vector_weight": 0.7,
                "rrf_k": 60,
            },
            "cache": {
                "enabled": True,
                "similarity_threshold": 0.95,
            },
        },
    },
    "speed": {
        "name": "快速响应方案",
        "description": "追求响应速度，关闭重排序和混合检索，减少计算开销",
        "config": {
            "retrieval": {
                "search_k": 8,
                "use_rerank": False,
                "score_threshold": 0.1,
            },
            "hybrid_search": {
                "enabled": False,
            },
            "cache": {
                "enabled": True,
                "similarity_threshold": 0.9,
            },
        },
    },
    "balanced": {
        "name": "均衡默认方案",
        "description": "恢复系统出厂默认配置",
        "config": None,  # special marker: use default Settings()
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_profiles() -> list[dict]:
    """Return a list of profile metadata (name, description) without full config."""
    return [
        {"id": key, "name": val["name"], "description": val["description"]}
        for key, val in _CONFIG_PROFILES.items()
    ]


def get_profile_config(profile_id: str) -> dict | None:
    """Return the full config dict for a given profile id.

    Returns None for unknown ids.
    For the 'balanced' profile, returns the default Settings() dict.
    """
    profile = _CONFIG_PROFILES.get(profile_id)
    if not profile:
        return None
    if profile["config"] is None:
        # balanced → return full defaults
        return Settings().model_dump()
    return profile["config"]


def validate_custom_profile(data: dict) -> tuple[bool, str]:
    """Validate a user-uploaded custom profile document.

    Expected format:
    {
        "name": "方案名称",
        "description": "方案描述",
        "config": { ... }   // partial config, same structure as PUT /config
    }

    Returns (is_valid, error_message).
    """
    if not isinstance(data, dict):
        return False, "方案文档必须是 JSON 对象"

    name = data.get("name", "")
    config = data.get("config")

    if not name or not isinstance(name, str):
        return False, "缺少 'name' 字段或类型错误"

    if config is None:
        return False, "缺少 'config' 字段"

    if not isinstance(config, dict):
        return False, "'config' 必须是对象"

    return True, ""
