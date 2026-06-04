"""Shared embedding service - loads model once, serves vectors via HTTP."""
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import torch
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("embedding")

model: SentenceTransformer = None
model_name = os.environ.get("EMBED_MODEL", "BAAI/bge-large-zh-v1.5")
device = "cuda" if torch.cuda.is_available() else "cpu"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    logger.info(f"Loading {model_name} on {device}...")
    model = SentenceTransformer(model_name, device=device)
    logger.info(f"Model loaded (dim={model.get_sentence_embedding_dimension()})")
    yield

app = FastAPI(lifespan=lifespan, title="Embedding Service")

class EmbedRequest(BaseModel):
    texts: list[str]

class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int

@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest):
    if model is None:
        raise HTTPException(503, "Model not loaded")
    vecs = model.encode(req.texts, normalize_embeddings=True, show_progress_bar=False)
    return EmbedResponse(embeddings=vecs.tolist(), dim=vecs.shape[1])

@app.get("/health")
async def health():
    return {"status": "ok", "model": model_name, "device": device}
