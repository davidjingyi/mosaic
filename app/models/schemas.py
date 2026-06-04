"""Pydantic data models for API requests and responses."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="Message role: user or assistant")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    query: str = Field(..., description="User query text")
    history: list[ChatMessage] = Field(default_factory=list, description="Chat history")
    stream: bool = Field(default=True, description="Whether to stream response")


class SourceDocument(BaseModel):
    content: str = Field(..., description="Document content snippet")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Document metadata")
    score: float = Field(0.0, description="Retrieval score")


class ChatResponse(BaseModel):
    answer: str = Field(..., description="Generated answer")
    sources: list[SourceDocument] = Field(default_factory=list, description="Source documents")
    cached: bool = Field(False, description="Whether result was from cache")


class DocumentInfo(BaseModel):
    doc_id: str = Field(..., description="Unique document ID")
    filename: str = Field(..., description="Original filename")
    upload_time: datetime | None = Field(None, description="Upload timestamp")
    chunk_count: int = Field(0, description="Number of chunks after splitting")
    file_size: int = Field(0, description="File size in bytes")


class RetrievalResult(BaseModel):
    content: str = Field(..., description="Retrieved content")
    metadata: dict[str, Any] = Field(default_factory=dict)
    similarity_score: float = Field(0.0)
    bm25_score: float = Field(0.0)
    rrf_score: float = Field(0.0)
    rerank_score: float = Field(0.0)


class UploadResponse(BaseModel):
    doc_id: str = Field(...)
    filename: str = Field(...)
    status: str = Field("success")
    message: str = Field("")


class KnowledgeBaseStats(BaseModel):
    total_indexed: int = 0
    total_chunks: int = 0
    total_size_bytes: int = 0
    total_size_mb: float = 0.0
    pdf_paths: list[str] = Field(default_factory=list)
    markdown_paths: list[str] = Field(default_factory=list)
    auto_scan: bool = False
    scan_interval_hours: int = 24
    files: list[dict[str, Any]] = Field(default_factory=list)


class ScanResult(BaseModel):
    success: bool = False
    scanned_paths: list[str] = Field(default_factory=list)
    total_files_found: int = 0
    imported: int = 0
    updated: int = 0
    removed: int = 0
    errors: list[str] = Field(default_factory=list)


class CacheStats(BaseModel):
    total_hits: int = 0
    total_misses: int = 0
    hit_rate: float = 0.0
    current_size: int = 0
    max_size: int = 1000


class CacheConfigUpdate(BaseModel):
    enabled: bool | None = None
    max_size: int | None = Field(None, ge=100, le=10000)
    similarity_threshold: float | None = Field(None, ge=0.5, le=1.0)
    default_ttl: int | None = None


class CacheInvalidateRequest(BaseModel):
    pattern: str | None = Field(None, description="Invalidate entries containing this pattern")


class ConfigUpdateRequest(BaseModel):
    data: dict[str, Any] = Field(..., description="Partial config update")


# ---------------------------------------------------------------------------
# Knowledge Base v2.1 Models
# ---------------------------------------------------------------------------

class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: str | None = Field(default=None)


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = Field(default=None)


class PreviewScanResult(BaseModel):
    file_path: str
    status: str = Field(..., pattern="^(new|modified|unchanged|missing)$")
    file_size: int = 0
    existing_doc_id: str | None = None
    expected_chunks_estimate: int = 0


class PreviewChunksRequest(BaseModel):
    file_paths: list[str] = Field(default_factory=list)
    folder_id: str | None = None


class PreviewChunksResult(BaseModel):
    doc_id: str
    file_path: str
    chunks: list[dict[str, Any]] = Field(default_factory=list)


class IngestRequest(BaseModel):
    doc_ids: list[str] = Field(default_factory=list)


class IngestProgress(BaseModel):
    job_id: str
    status: str = Field(..., pattern="^(pending|running|completed|failed)$")
    total: int = 0
    current: int = 0
    current_file: str = ""
    errors: list[str] = Field(default_factory=list)


class BatchActionRequest(BaseModel):
    action: str = Field(..., pattern="^(delete|move|rechunk)$")
    doc_ids: list[str] = Field(default_factory=list)
    folder_id: str | None = None


# ---------------------------------------------------------------------------
# Chunk Management Models
# ---------------------------------------------------------------------------

class ChunkUpdateRequest(BaseModel):
    content: str | None = Field(default=None)
    metadata: dict | None = Field(default=None)


class ChunkDetail(BaseModel):
    chunk_id: str
    document: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    doc_id: str = ""
    chunk_index: int = 0
