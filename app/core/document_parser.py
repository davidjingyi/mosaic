"""Document parsing with support for PDF, TXT, MD, DOCX."""
import logging
from pathlib import Path

import chardet
from langchain_core.documents import Document
from langchain_community.document_loaders import (
    Docx2txtLoader,
    PyPDFLoader,
)

from app.core.interfaces import DocumentParser

logger = logging.getLogger(__name__)


def _load_text_file(file_path: str) -> str:
    """Load a text file with automatic encoding detection."""
    # Try UTF-8 first
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        pass

    # Detect encoding with chardet
    with open(file_path, "rb") as f:
        raw = f.read()
        result = chardet.detect(raw)
        encoding = result.get("encoding") or "utf-8"

    return raw.decode(encoding, errors="replace")


class DefaultDocumentParser(DocumentParser):
    """Parse supported files into LangChain Documents."""

    def parse(self, file_path: Path) -> list[Document]:
        ext = file_path.suffix.lower()
        documents: list[Document] = []

        if ext == ".pdf":
            loader = PyPDFLoader(str(file_path))
            try:
                documents = loader.load()
            finally:
                # Best-effort cleanup to avoid file handle leaks
                if hasattr(loader, 'close'):
                    loader.close()  # type: ignore[attr-defined]
        elif ext in (".txt", ".md"):
            text = _load_text_file(str(file_path))
            documents = [Document(page_content=text)]
        elif ext == ".docx":
            loader = Docx2txtLoader(str(file_path))
            try:
                documents = loader.load()
            finally:
                if hasattr(loader, 'close'):
                    loader.close()  # type: ignore[attr-defined]
        else:
            raise ValueError(f"Unsupported file type: {ext}. Supported: .pdf, .txt, .md, .docx")

        for doc in documents:
            doc.metadata["source"] = file_path.name
            doc.metadata["file_path"] = str(file_path)
            doc.metadata["doc_type"] = ext.lstrip(".")
        logger.info("Parsed %s: %d pages", file_path.name, len(documents))
        return documents
