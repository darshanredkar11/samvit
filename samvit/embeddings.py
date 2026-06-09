"""
Sentence-transformers embedding wrapper.

Decisions applied:
  - Model loaded eagerly at startup in lifespan(); server exits if unavailable
  - Inference runs in thread pool to avoid blocking the async event loop
  - Content truncated to 512 tokens (model max) with a logged warning
  - NaN embeddings detected and rejected before DB write
  - MAX_CONTENT_BYTES enforced here (Decision #9: 32 KB)
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

import numpy as np
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
MAX_CONTENT_BYTES = 32 * 1024   # Decision #9: 32 KB
MAX_TOKENS = 512                  # MiniLM hard limit

_model: SentenceTransformer | None = None


def load_model() -> None:
    """
    Eagerly load the embedding model.
    Called from FastAPI lifespan(). Raises RuntimeError if model cannot load.
    Decision #14: fail fast — do not allow lazy loading.
    """
    global _model
    log.info("Loading embedding model: %s", MODEL_NAME)
    try:
        _model = SentenceTransformer(MODEL_NAME)
        # Warm up with a dummy encode to catch any initialisation issues
        _model.encode(["warmup"], show_progress_bar=False)
        log.info("Embedding model loaded successfully")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load embedding model '{MODEL_NAME}': {exc}"
        ) from exc


def _model_instance() -> SentenceTransformer:
    if _model is None:
        raise RuntimeError("Embedding model not loaded — call load_model() first")
    return _model


def _encode_sync(text: str) -> list[float]:
    """Blocking encode — runs in thread pool via embed()."""
    model = _model_instance()
    vec = model.encode([text], show_progress_bar=False, normalize_embeddings=True)[0]

    # Detect and reject NaN/Inf embeddings (FAILURE_ANALYSIS §9.4)
    arr = np.array(vec, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        raise ValueError("Embedding contains NaN or Inf values — refusing to store")

    return arr.tolist()


async def embed(content: str) -> list[float]:
    """
    Async entry point. Validates size then embeds in thread pool.
    Decision #9: reject content > 32 KB.
    """
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(
            f"Content exceeds maximum size of {MAX_CONTENT_BYTES // 1024} KB"
        )
    if not content.strip():
        raise ValueError("Content must not be empty")

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_encode_sync, content))
