"""
癌种关键词加权检索增强器 (Cancer Type Booster)

原理：
1. 从用户 query 中检测癌种
2. 对检索结果中元数据匹配该癌种的文档做分数加权
3. 加权后重新排序，让目标癌种文档优先于无关文档
"""

import logging
from typing import Any

from app.config import CancerBoostingConfig

logger = logging.getLogger(__name__)


class CancerTypeBooster:
    """检索后癌种加权重排序器。"""

    def __init__(self, config: CancerBoostingConfig):
        self.config = config
        # Build reverse index: keyword → cancer_type
        self._keyword_to_type: dict[str, str] = {}
        for cancer_type, keywords in config.cancer_keywords.items():
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower not in self._keyword_to_type:
                    self._keyword_to_type[kw_lower] = cancer_type
                # Longer keywords take priority (more specific match)
                elif len(kw) > len(self._keyword_to_type[kw_lower]):
                    self._keyword_to_type[kw_lower] = cancer_type

        # Sort keywords by length descending so we match longest first
        self._sorted_keywords = sorted(
            self._keyword_to_type.keys(),
            key=len,
            reverse=True,
        )

    def detect_cancer_types(self, query: str) -> list[str]:
        """Detect cancer types mentioned in the query.

        Returns list of cancer type names, ordered by match priority.
        Longer keyword matches take precedence.
        """
        query_lower = query.lower()
        matched_types: dict[str, int] = {}  # cancer_type -> total keyword length sum

        for kw in self._sorted_keywords:
            if kw in query_lower:
                ctype = self._keyword_to_type[kw]
                matched_types[ctype] = matched_types.get(ctype, 0) + len(kw)

        if not matched_types:
            return []

        # Sort by total keyword match length (more specific matches first)
        sorted_types = sorted(matched_types.items(), key=lambda x: x[1], reverse=True)
        return [t[0] for t in sorted_types]

    def augment_query(self, query: str) -> str:
        """Prepend cancer-type keywords to guide embedding/BM25 toward target docs.

        Example: '乳腺癌HER2方案' → '乳腺癌 乳腺癌 乳腺癌 乳腺癌 HER2方案'
        The repetition gives cancer-type terms dominant weight in the embedding vector.
        """
        if not self.config.enabled:
            return query

        cancer_types = self.detect_cancer_types(query)
        if not cancer_types:
            return query

        # Use the primary (most specific) cancer type
        primary = cancer_types[0]
        # Always use the cancer type name itself as the boost keyword,
        # falling back to the first keyword in the list
        best_kw = primary
        # If the cancer type name is too long/specific, use a shorter common name
        # e.g., "非小细胞肺癌" → "肺癌"
        keywords = self.config.cancer_keywords.get(primary, [primary])
        if keywords and len(keywords[0]) < len(primary):
            best_kw = keywords[0]

        # Repeat cancer keyword 4x to dominate the embedding vector
        augmented = f"{best_kw} {best_kw} {best_kw} {best_kw} {query}"
        logger.info(
            "CancerBooster: augmented query for '%s': '%s...'",
            primary, augmented[:80],
        )
        return augmented

    def _doc_matches_type(self, doc, cancer_type: str) -> bool:
        """Check if a document matches a cancer type (metadata + content)."""
        if cancer_type not in self.config.cancer_keywords:
            return False

        keywords = self.config.cancer_keywords[cancer_type]
        
        # Check metadata fields
        meta = getattr(doc, "metadata", doc) if hasattr(doc, "metadata") else doc
        meta_text = " ".join([
            str(meta.get("title", "")),
            str(meta.get("source", "")),
            str(meta.get("doc_id", "")),
        ]).lower()
        
        for kw in keywords:
            if kw.lower() in meta_text:
                return True
        
        # Also check page_content (first 300 chars)
        content = getattr(doc, "page_content", "") or doc.get("page_content", "") or ""
        content_lower = content[:300].lower()
        for kw in keywords:
            if kw.lower() in content_lower:
                return True
                
        return False

    def boost(
        self,
        query: str,
        documents: list[Any],
    ) -> list[Any]:
        """Boost documents matching detected cancer types and re-sort.

        Args:
            query: User's original query
            documents: Retrieved documents (langchain Document or dict)

        Returns:
            Re-sorted documents with boosted scores
        """
        if not self.config.enabled or not documents:
            return documents

        cancer_types = self.detect_cancer_types(query)
        if not cancer_types:
            logger.debug("CancerBooster: no cancer type detected in query")
            return documents

        logger.info(
            "CancerBooster: detected types=%s, multiplier=%.2f",
            cancer_types, self.config.boost_multiplier,
        )

        boosted = []
        for doc in documents:
            matched = False
            for ctype in cancer_types:
                if self._doc_matches_type(doc, ctype):
                    matched = True
                    break

            # Boost score
            if matched:
                if hasattr(doc, "metadata"):
                    old_score = doc.metadata.get("score", 0.0)
                    new_score = min(old_score * self.config.boost_multiplier, 1.0)
                    doc.metadata["score"] = new_score
                    doc.metadata["cancer_boosted"] = True
                    doc.metadata["cancer_type"] = cancer_types[0]
                    logger.debug(
                        "CancerBooster: boosted doc, type=%s, score %.4f → %.4f",
                        cancer_types[0], old_score, new_score,
                    )
                else:
                    old_score = doc.get("score", 0.0)
                    new_score = min(old_score * self.config.boost_multiplier, 1.0)
                    doc["score"] = new_score
                    doc["cancer_boosted"] = True
                    doc["cancer_type"] = cancer_types[0]
                    logger.debug(
                        "CancerBooster: boosted doc, type=%s, score %.4f → %.4f",
                        cancer_types[0], old_score, new_score,
                    )

            boosted.append(doc)

        # Re-sort by (boosted) score descending
        def _get_score(doc):
            if hasattr(doc, "metadata"):
                return doc.metadata.get("score", 0.0)
            return doc.get("score", 0.0) if isinstance(doc, dict) else 0.0

        boosted.sort(key=_get_score, reverse=True)

        return boosted
