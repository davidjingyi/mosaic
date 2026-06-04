"""Remote embedder - calls shared embedding service via HTTP instead of loading model locally.

Set EMBEDDING_SERVICE_URL env var to enable this instead of STEmbedder.
"""
import logging
import os

import httpx

from app.core.interfaces import Embedder

logger = logging.getLogger(__name__)

EMBEDDING_SERVICE_URL = os.environ.get("EMBEDDING_SERVICE_URL", "").rstrip("/")


class RemoteEmbedder(Embedder):
    """Calls a shared embedding service via HTTP.  
    Avoids loading the 1.3GB model in every worker process."""

    def __init__(self, service_url: str = "", timeout: float = 300.0):
        self.service_url = service_url or EMBEDDING_SERVICE_URL
        self._client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.post(
            f"{self.service_url}/embed",
            json={"texts": texts},
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]

    def embed_query(self, query: str) -> list[float]:
        results = self.embed([query])
        return results[0] if results else []

    @property
    def dimension(self) -> int:
        return 1024  # bge-large-zh-v1.5
