"""
Token generation, hashing, and verification.

Decisions applied:
  - Tokens are 48-byte url-safe base64 strings prefixed with 'samvit_'
  - bcrypt hashing runs in a thread-pool executor (never blocks event loop)
  - Comparison uses hmac.compare_digest to prevent timing attacks
  - Authorization header is NEVER logged (redacted at middleware level)
  - Handles are normalised to lowercase before any DB operation
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac

import asyncpg
import logging
import re
import secrets
from functools import partial

import bcrypt

from samvit import db

log = logging.getLogger(__name__)

HANDLE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
TOKEN_PREFIX = "samvit_"


# ── Token helpers ─────────────────────────────────────────────────────────────

def generate_token() -> str:
    """Return a new random bearer token."""
    return TOKEN_PREFIX + secrets.token_urlsafe(48)


def _token_sha256(token: str) -> str:
    """Deterministic SHA-256 hash for fast index lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


async def hash_token(token: str) -> str:
    """bcrypt-hash a token. Runs in thread pool to avoid blocking the loop."""
    loop = asyncio.get_running_loop()
    hashed: bytes = await loop.run_in_executor(
        None, partial(bcrypt.hashpw, token.encode(), bcrypt.gensalt(rounds=12))
    )
    return hashed.decode()


async def verify_token_hash(token: str, hashed: str) -> bool:
    """Constant-time bcrypt check. Runs in thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(bcrypt.checkpw, token.encode(), hashed.encode()),
    )


# ── Agent lookup ──────────────────────────────────────────────────────────────

async def authenticate(raw_token: str) -> dict | None:
    """
    Validate a bearer token and return the agent row, or None.
    Uses SHA-256 index for fast lookup, then bcrypt to confirm.
    Strips whitespace before comparison (Decision #1 annex).
    Never logs the token value.
    """
    token = raw_token.strip()
    if not token.startswith(TOKEN_PREFIX):
        return None

    sha256 = _token_sha256(token)

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, handle, provider, role, suspended_at, workspace_id, token_hash FROM agents WHERE token_hash_sha256 = $1",
            sha256,
        )
        if row is None:
            return None

    if await verify_token_hash(token, row["token_hash"]):
        return dict(row)

    return None


# ── Registration helpers ───────────────────────────────────────────────────────

def validate_handle(handle: str) -> str:
    """
    Normalise and validate a handle.
    Returns lowercased handle or raises ValueError.
    Decision #1: ^[a-z0-9_-]{1,64}$
    Decision #2: normalise to lowercase
    """
    normalised = handle.lower().strip()
    if not HANDLE_RE.match(normalised):
        raise ValueError(
            f"Handle must match ^[a-z0-9_-]{{1,64}}$ (got {handle!r})"
        )
    return normalised


async def register_agent(handle: str, provider: str) -> dict:
    """
    Register a new agent. Returns {'agent_id', 'token'}.
    Raises ValueError on duplicate handle or invalid inputs.
    """
    if not provider or not provider.strip():
        raise ValueError("provider must not be empty")

    normalised = validate_handle(handle)
    token = generate_token()
    token_hash = await hash_token(token)
    sha256 = _token_sha256(token)

    async with db.pool().acquire() as conn:
        default_ws = await conn.fetchval("SELECT id FROM workspaces WHERE name = 'default'")
        try:
            agent_id = await conn.fetchval(
                """
                INSERT INTO agents (handle, provider, token_hash, token_hash_sha256, workspace_id)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                normalised,
                provider.strip(),
                token_hash,
                sha256,
                default_ws,
            )
        except asyncpg.UniqueViolationError:
            raise ValueError(f"Handle '{normalised}' is already registered")

    log.info("Agent registered: handle=%s provider=%s", normalised, provider)
    return {"agent_id": str(agent_id), "token": token}


async def rotate_token(agent_id: str) -> str:
    """
    Issue a new token for agent_id and atomically invalidate the old one.
    Returns the new raw token.
    """
    new_token = generate_token()
    new_hash = await hash_token(new_token)
    new_sha256 = _token_sha256(new_token)

    async with db.pool().acquire() as conn:
        result = await conn.execute(
            "UPDATE agents SET token_hash = $1, token_hash_sha256 = $2 WHERE id = $3",
            new_hash, new_sha256, agent_id,
        )
        if result == "UPDATE 0":
            raise ValueError("Agent not found")

    log.info("Token rotated for agent_id=%s", agent_id)
    return new_token


async def admin_reset_token(handle: str, admin_secret: str) -> str:
    """
    Admin-only token reset. Validates SAMVIT_ADMIN_SECRET.
    Decision #12: escape hatch when agent loses token.
    """
    import os
    expected = os.environ.get("SAMVIT_ADMIN_SECRET", "")
    if not expected:
        raise PermissionError("SAMVIT_ADMIN_SECRET is not configured. Cannot reset token without admin secret.")
    if not hmac.compare_digest(admin_secret, expected):
        raise PermissionError("Invalid admin secret")

    normalised = validate_handle(handle)
    new_token = generate_token()
    new_hash = await hash_token(new_token)
    new_sha256 = _token_sha256(new_token)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            agent_id = await conn.fetchval(
                "SELECT id FROM agents WHERE handle = $1 FOR UPDATE",
                normalised,
            )
            if not agent_id:
                raise ValueError(f"Agent '{normalised}' not found")
            await conn.execute(
                "UPDATE agents SET token_hash = $1, token_hash_sha256 = $2 WHERE id = $3",
                new_hash, new_sha256, agent_id,
            )

    log.info("Admin reset token for handle=%s", normalised)
    return new_token


# ── Log filter — redact Authorization header ──────────────────────────────────

class RedactAuthFilter(logging.Filter):
    """
    Decision #10: strip Authorization header value from all log records.
    Attach to root logger or uvicorn access logger.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = record.msg.replace(
                record.__dict__.get("Authorization", ""), "[REDACTED]"
            )
        return True
