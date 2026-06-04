"""Text chunking with document-type-aware rules and contextual enrichment."""
import logging
import re
from pathlib import Path
from typing import Tuple

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import ChunkingConfig, DocTypeRule, Settings
from app.core.interfaces import Chunker

logger = logging.getLogger(__name__)

HEADING_PATTERNS = [
    (r"^#{1,6}\s+(.+)$", "markdown"),
    (r"^\s*(\d+[.、])\s*(.+)$", "numbered"),
    (r"^\s*第[一二三四五六七八九十\d]+章\s*[、.:\s]*\s*(.*)$", "chapter"),
    (r"^\s*第[一二三四五六七八九十\d]+节\s*[、.:\s]*\s*(.*)$", "section"),
]

SKIP_PATH_PARTS = {
    "", ".", "..", "documents", "docs", "files", "data",
    "backup", "document", "doc", "file", "newfolder", "users",
    "desktop", "home", "mnt",
}


class DefaultChunker(Chunker):
    """Chunk documents with doc-type-aware sizing and optional contextual prefixes."""

    def __init__(self, config: ChunkingConfig, rules: list[DocTypeRule] | None = None):
        self.config = config
        self.rules = rules or []
        self._default_splitter = self._build_splitter(config.chunk_size, config.chunk_overlap)

    def _build_splitter(self, chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[self.config.separator, "\n", "。", ".", " ", ""],
            length_function=len,
        )

    def _detect_rule(self, file_path: Path) -> DocTypeRule | None:
        name_lower = file_path.name.lower()
        for rule in self.rules:
            for kw in rule.keywords:
                if kw.lower() in name_lower:
                    return rule
        return None

    def _get_params(self, file_path: Path | None) -> Tuple[int, int]:
        if file_path is None:
            return self.config.chunk_size, self.config.chunk_overlap
        rule = self._detect_rule(file_path)
        if rule:
            logger.info("Doc-type rule matched for %s: size=%d", file_path.name, rule.chunk_size)
            return rule.chunk_size, rule.chunk_overlap
        return self.config.chunk_size, self.config.chunk_overlap

    def _extract_structure(self, text: str) -> list[Tuple[int, int, str, str]]:
        structures = []
        for pattern, htype in HEADING_PATTERNS:
            for m in re.finditer(pattern, text, re.MULTILINE):
                heading = m.group(1).strip() if htype == "markdown" else m.group(0).strip()
                if htype == "numbered":
                    heading = m.group(2).strip()
                structures.append((m.start(), m.end(), heading, htype))
        structures.sort(key=lambda x: x[0])
        return structures

    def _find_section(
        self, structures: list[Tuple[int, int, str, str]], pos: int
    ) -> Tuple[str, str]:
        if not structures:
            return ("", "")
        best_section, best_type, best_dist = structures[0][2], structures[0][3], float("inf")
        for start, _, heading, htype in structures:
            if start <= pos:
                dist = pos - start
                if dist < best_dist:
                    best_dist = dist
                    best_section = heading
                    best_type = htype
        return best_section, best_type

    def _extract_path_tags(self, file_path: Path | None) -> list[str]:
        if file_path is None:
            return []
        tags = []
        for part in file_path.parent.parts:
            p = part.strip()
            if p.lower() in SKIP_PATH_PARTS:
                continue
            if len(p) == 2 and p[1] == ":":
                continue
            if len(p) == 36 and p.count("-") == 4:
                continue
            if p.isdigit():
                continue
            tags.append(p)
        return tags

    def _apply_context(
        self,
        chunks: list[Document],
        full_text: str,
        doc_title: str,
        doc_type: str,
        file_path: Path | None,
    ) -> list[Document]:
        if not self.config.enable_contextual_chunking:
            for i, chunk in enumerate(chunks):
                chunk.metadata["chunk_index"] = i
                chunk.metadata["total_chunks"] = len(chunks)
            return chunks

        structures = self._extract_structure(full_text)
        path_tags = self._extract_path_tags(file_path)
        path_tags_str = " > ".join(path_tags) if path_tags else ""
        total = len(chunks)
        enriched = []

        for i, chunk in enumerate(chunks):
            text = chunk.page_content
            # Use accumulated offset from previous chunks for more reliable positioning
            # instead of searching full_text which may have duplicates
            search_key = text[:50] if len(text) >= 50 else text
            try:
                pos = full_text.index(search_key, i * self.config.chunk_size // 2)
            except ValueError:
                pos = i * self.config.chunk_size // 2
            section, section_type = self._find_section(structures, pos)
            ctx = {
                "doc_title": doc_title or "未知文档",
                "section": section or "未分类",
                "position": i + 1,
                "total": total,
                "content": text,
                "doc_type": doc_type,
                "path_tags": path_tags_str,
            }
            try:
                if not self.config.contextual_prefix_format or not self.config.contextual_prefix_format.strip():
                    prefixed = (
                        f"[文档: {doc_title}][分类: {path_tags_str}]"
                        f"[章节: {section}][位置: {i + 1}/{total}]\n{text}"
                    )
                else:
                    prefixed = self.config.contextual_prefix_format.format(**ctx)
            except (KeyError, ValueError):
                prefixed = (
                    f"[文档: {doc_title}][分类: {path_tags_str}]"
                    f"[章节: {section}][位置: {i + 1}/{total}]\n{text}"
                )
            enriched.append(
                Document(
                    page_content=prefixed,
                    metadata={
                        **chunk.metadata,
                        "chunk_index": i,
                        "total_chunks": total,
                        "section_title": section,
                        "section_type": section_type,
                        "doc_title": doc_title,
                        "has_contextual_prefix": True,
                        "path_tags": path_tags,
                        "original_content": text,
                    },
                )
            )
        return enriched

    def chunk(self, documents: list[Document], file_path: Path | None = None) -> list[Document]:
        chunk_size, chunk_overlap = self._get_params(file_path)
        splitter = (
            self._default_splitter
            if (chunk_size, chunk_overlap) == (self.config.chunk_size, self.config.chunk_overlap)
            else self._build_splitter(chunk_size, chunk_overlap)
        )
        full_text = "\n\n".join(d.page_content for d in documents)
        doc_title = documents[0].metadata.get("source", "") if documents else ""
        doc_type = documents[0].metadata.get("doc_type", "") if documents else ""
        chunks = splitter.split_documents(documents)
        result = self._apply_context(chunks, full_text, doc_title, doc_type, file_path)
        logger.info("Chunked %s into %d chunks (size=%d)", file_path.name if file_path else "?", len(result), chunk_size)
        return result
