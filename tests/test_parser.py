"""Tests for document parser."""
from pathlib import Path

import pytest

from app.core.document_parser import DefaultDocumentParser


def test_unsupported_extension():
    parser = DefaultDocumentParser()
    with pytest.raises(ValueError, match="Unsupported"):
        parser.parse(Path("test.xls"))


def test_parse_txt(tmp_path):
    parser = DefaultDocumentParser()
    file_path = tmp_path / "test.txt"
    file_path.write_text("Hello world\nSecond line", encoding="utf-8")
    docs = parser.parse(file_path)
    assert len(docs) == 1
    assert "Hello world" in docs[0].page_content
    assert docs[0].metadata["source"] == "test.txt"
