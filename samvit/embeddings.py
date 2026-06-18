"""
Fastembed embedding wrapper.

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
from fastembed import TextEmbedding

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MAX_CONTENT_BYTES = 32 * 1024   # Decision #9: 32 KB
MAX_TOKENS = 512                  # model hard limit

_model: TextEmbedding | None = None


def load_model() -> None:
    global _model
    log.info("Loading embedding model: %s", MODEL_NAME)
    try:
        _model = TextEmbedding(MODEL_NAME, max_length=MAX_TOKENS)
        list(_model.embed(["warmup"]))
        log.info("Embedding model loaded successfully")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load embedding model '{MODEL_NAME}': {exc}"
        ) from exc


def _model_instance() -> TextEmbedding:
    if _model is None:
        raise RuntimeError("Embedding model not loaded — call load_model() first")
    return _model


def _encode_sync(text: str) -> list[float]:
    model = _model_instance()
    vec = next(model.embed([text])).astype(np.float64)

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    if not np.all(np.isfinite(vec)):
        raise ValueError("Embedding contains NaN or Inf values — refusing to store")

    return vec.tolist()


def fmt_vector(v: list[float]) -> str:
    return "[" + ",".join(str(x) for x in v) + "]"


async def embed(content: str) -> list[float]:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError(
            f"Content exceeds maximum size of {MAX_CONTENT_BYTES // 1024} KB"
        )
    if not content.strip():
        raise ValueError("Content must not be empty")

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_encode_sync, content))
