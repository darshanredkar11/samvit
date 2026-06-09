"""
SamvitWorker — base class for harness-based agent workers.

Every agent that subclasses SamvitWorker gets:
  • Auto-registration / token persistence (~/.samvit/credentials.json)
  • Retry-with-backoff on startup if Samvit is unreachable
  • Main run loop: claim → execute → done (with auto-retry on failure)
  • Convenience wrappers: remember(), recall(), say(), read()
  • Context injection: recent memories loaded before each task
  • Graceful shutdown on SIGINT/SIGTERM

Usage:
    class ReviewWorker(SamvitWorker):
        async def execute(self, task: dict) -> dict:
            pr_url = task["description"]
            verdict = await my_llm_review(pr_url)
            await self.remember(f"Reviewed {pr_url}: {verdict}", key=f"review.{pr_url}")
            return {"verdict": verdict}

    asyncio.run(ReviewWorker(handle="rahul", provider="antigravity").run(tags=["review"]))
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

CREDENTIALS_PATH = Path.home() / ".samvit" / "credentials.json"
DEFAULT_URL       = os.environ.get("SAMVIT_URL", "http://localhost:8765")
POLL_INTERVAL     = float(os.environ.get("SAMVIT_POLL_INTERVAL", "3"))   # seconds
STARTUP_RETRIES   = int(os.environ.get("SAMVIT_STARTUP_RETRIES", "10"))
STARTUP_BACKOFF   = float(os.environ.get("SAMVIT_STARTUP_BACKOFF", "3"))
CONTEXT_LIMIT     = int(os.environ.get("SAMVIT_CONTEXT_LIMIT", "5"))


class SamvitWorker:
    """
    Base class for all Samvit harness workers.
    Override execute() in your subclass.
    """

    def __init__(
        self,
        handle: str,
        provider: str,
        samvit_url: str = DEFAULT_URL,
    ) -> None:
        self.handle     = handle.lower().strip()
        self.provider   = provider
        self.samvit_url = samvit_url.rstrip("/")
        self._token:    str | None = None
        self._agent_id: str | None = None
        self._http:     httpx.AsyncClient | None = None
        self._running   = True

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Connect to Samvit: load or create credentials, verify token."""
        self._http = httpx.AsyncClient(timeout=10)
        self._token, self._agent_id = await self._load_or_register()
        log.info("[%s] Connected to Samvit at %s", self.handle, self.samvit_url)

    async def shutdown(self) -> None:
        self._running = False
        if self._http:
            await self._http.aclose()
        log.info("[%s] Worker shut down", self.handle)

    async def run(
        self,
        tags: list[str] | None = None,
        worker_type: str | None = None,
        max_concurrent: int = 1,
    ) -> None:
        """Main loop: claim → execute → done. Handles SIGINT/SIGTERM gracefully."""
        await self._wait_for_samvit()
        await self.startup()
        await self.on_start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: setattr(self, "_running", False))
            except NotImplementedError:
                pass  # Windows

        semaphore = asyncio.Semaphore(max_concurrent)

        while self._running:
            task = await self._claim(tags=tags, worker_type=worker_type)
            if not task:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # Load recent context before execution
            context = await self._load_context(task)

            async with semaphore:
                await self._run_task(task, context)

        await self.shutdown()

    async def _run_task(self, task: dict, context: list[dict]) -> None:
        tid   = task["id"]
        token = task["claim_token"]
        log.info("[%s] Starting task: %s", self.handle, task["title"])
        try:
            result = await self.execute(task, context=context)
            await self._done(tid, token, result=result, status="done")
            log.info("[%s] Task done: %s", self.handle, task["title"])
        except Exception as exc:
            log.error("[%s] Task failed: %s — %s", self.handle, task["title"], exc)
            await self._done(tid, token, result={"error": str(exc)}, status="failed")

    # ── Override these ────────────────────────────────────────────────────────

    async def execute(self, task: dict, context: list[dict] | None = None) -> dict:
        """Override in subclass. Return a result dict."""
        raise NotImplementedError("Subclasses must implement execute()")

    async def on_start(self) -> None:
        """Called once after successful startup. Override for init logic."""
        pass

    # ── Convenience tool wrappers ─────────────────────────────────────────────

    async def remember(self, content: str, key: str | None = None,
                       namespace: str = "global", metadata: dict | None = None) -> dict:
        return await self._post("/mcp/call", {
            "tool": "remember",
            "params": {"content": content, "key": key,
                       "namespace": namespace, "metadata": metadata or {}},
        })

    async def recall(self, query: str | None = None, key: str | None = None,
                     namespace: str = "global", limit: int = 5) -> list[dict]:
        r = await self._post("/mcp/call", {
            "tool": "recall",
            "params": {"query": query, "key": key,
                       "namespace": namespace, "limit": limit},
        })
        return r.get("results", [])

    async def say(self, body: str, to: str | None = None, topic: str | None = None) -> dict:
        return await self._post("/mcp/call", {
            "tool": "say",
            "params": {"body": body, "to": to, "topic": topic},
        })

    async def read(self, mark_read: bool = True, limit: int = 20) -> list[dict]:
        r = await self._post("/mcp/call", {
            "tool": "read",
            "params": {"mark_read": mark_read, "limit": limit},
        })
        return r.get("messages", [])

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _wait_for_samvit(self) -> None:
        """Retry with backoff until Samvit health endpoint responds."""
        async with httpx.AsyncClient(timeout=5) as c:
            for attempt in range(1, STARTUP_RETRIES + 1):
                try:
                    r = await c.get(f"{self.samvit_url}/health")
                    if r.status_code == 200:
                        return
                except Exception:
                    pass
                log.warning("[%s] Samvit not ready (attempt %d/%d), retrying in %.0fs…",
                            self.handle, attempt, STARTUP_RETRIES, STARTUP_BACKOFF)
                await asyncio.sleep(STARTUP_BACKOFF)
        raise RuntimeError(f"Samvit unreachable at {self.samvit_url} after {STARTUP_RETRIES} attempts")

    async def _load_or_register(self) -> tuple[str, str]:
        """
        Load credentials from disk, verify them, re-register if stale.
        Credentials stored at ~/.samvit/credentials.json (mode 0600).

        Failure modes handled:
          - File missing → register fresh
          - File malformed JSON → register fresh (log warning)
          - Token invalid (401) → register fresh (token was rotated externally)
          - Handle already registered but token unknown → admin reset not available
            here → raise with clear message
        """
        creds = self._read_credentials()
        if creds and creds.get("handle") == self.handle:
            # Verify the token is still valid
            token = creds["token"]
            agent_id = creds.get("agent_id", "")
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(
                        f"{self.samvit_url}/health",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                if r.status_code != 401:
                    log.debug("[%s] Loaded credentials from disk", self.handle)
                    return token, agent_id
                log.warning("[%s] Stored token rejected (401) — re-registering", self.handle)
            except Exception:
                pass  # network issue, try to register anyway

        # Register (409 = handle taken, surface clear error)
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.samvit_url}/v1/agents/register",
                    json={"handle": self.handle, "provider": self.provider},
                )
            if r.status_code == 201:
                data = r.json()
                self._write_credentials(data["token"], data["agent_id"])
                log.info("[%s] Registered as new agent", self.handle)
                return data["token"], data["agent_id"]
            if r.status_code == 409:
                raise RuntimeError(
                    f"Handle '{self.handle}' is already registered but this machine "
                    f"has no valid credentials for it. "
                    f"If you lost your token, use: "
                    f"POST {self.samvit_url}/v1/admin/agents/{self.handle}/reset"
                )
            raise RuntimeError(f"Registration failed: {r.status_code} {r.text}")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not connect to Samvit: {exc}") from exc

    def _read_credentials(self) -> dict | None:
        if not CREDENTIALS_PATH.exists():
            return None
        try:
            data = json.loads(CREDENTIALS_PATH.read_text())
            # Support per-handle entries
            if isinstance(data, dict) and "token" in data:
                return data
            if isinstance(data, dict):
                return data.get(self.handle)
            return None
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read credentials file (%s), will re-register", exc)
            return None

    def _write_credentials(self, token: str, agent_id: str) -> None:
        CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if CREDENTIALS_PATH.exists():
            try:
                existing = json.loads(CREDENTIALS_PATH.read_text())
            except Exception:
                existing = {}
        existing[self.handle] = {"handle": self.handle, "token": token, "agent_id": agent_id}
        CREDENTIALS_PATH.write_text(json.dumps(existing, indent=2))
        CREDENTIALS_PATH.chmod(0o600)   # owner read/write only

    async def _claim(self, tags: list[str] | None, worker_type: str | None) -> dict | None:
        claim_tags = list(tags or [])
        if worker_type:
            claim_tags.append(worker_type)
        try:
            r = await self._post("/mcp/call", {
                "tool": "claim",
                "params": {"tags": claim_tags or None},
            })
            return r.get("task")
        except Exception as exc:
            log.debug("[%s] Claim error: %s", self.handle, exc)
            return None

    async def _done(self, task_id: str, claim_token: str,
                    result: dict | None, status: str) -> None:
        try:
            await self._post("/mcp/call", {
                "tool": "done",
                "params": {"task_id": task_id, "claim_token": claim_token,
                           "result": result, "status": status},
            })
        except Exception as exc:
            log.error("[%s] Failed to mark task %s as %s: %s", self.handle, task_id, status, exc)

    async def _load_context(self, task: dict) -> list[dict]:
        """Recall relevant memories to inject as context before execution."""
        query = f"{task.get('title', '')} {task.get('description', '')}".strip()
        if not query:
            return []
        try:
            return await self.recall(query=query, limit=CONTEXT_LIMIT)
        except Exception:
            return []

    async def _post(self, path: str, body: dict) -> dict:
        if not self._http or not self._token:
            raise RuntimeError("Worker not started — call startup() first")
        r = await self._http.post(
            f"{self.samvit_url}{path}",
            json=body,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        r.raise_for_status()
        return r.json()
