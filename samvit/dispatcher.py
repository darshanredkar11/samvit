"""
SamvitDispatcher — reads the Samvit task queue and spawns typed workers.

Workers are registered by worker_type tag. The dispatcher claims tasks and
routes them to the matching worker class.

Failure modes handled:
  - Unregistered task type → task is skipped (not claimed)
  - Worker raises exception → task marked failed, dispatcher continues
  - Max concurrency per type → back-pressure prevents queue stampede
  - Dispatcher restart → in-flight tasks timeout + auto-release (cleanup.py)
  - Two dispatchers running → SKIP LOCKED ensures no double-claim

Usage:
    dispatcher = SamvitDispatcher(handle="dispatcher", provider="system")

    @dispatcher.worker("review")
    class ReviewWorker(SamvitWorker):
        async def execute(self, task, context=None):
            return {"reviewed": True}

    @dispatcher.worker("backend")
    class BackendWorker(SamvitWorker):
        async def execute(self, task, context=None):
            return {"built": True}

    asyncio.run(dispatcher.run())
"""

from __future__ import annotations

import asyncio
import logging
from typing import Type

import httpx

from samvit.harness import SamvitWorker, DEFAULT_URL, POLL_INTERVAL, STARTUP_BACKOFF, STARTUP_RETRIES

log = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = int(__import__("os").environ.get("SAMVIT_DISPATCHER_MAX", "4"))


class SamvitDispatcher:
    """
    Polls the Samvit queue and routes tasks to registered worker classes.
    Each worker_type gets its own concurrency semaphore.
    """

    def __init__(
        self,
        handle: str = "dispatcher",
        provider: str = "system",
        samvit_url: str = DEFAULT_URL,
        poll_interval: float = POLL_INTERVAL,
        max_concurrent_per_type: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self.handle      = handle
        self.provider    = provider
        self.samvit_url  = samvit_url.rstrip("/")
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent_per_type
        self._workers:    dict[str, Type[SamvitWorker]] = {}
        self._semaphores: dict[str, asyncio.Semaphore]  = {}
        self._running     = True

    def register(self, worker_type: str, worker_cls: Type[SamvitWorker]) -> None:
        """Register a worker class for a task type."""
        self._workers[worker_type] = worker_cls
        self._semaphores[worker_type] = asyncio.Semaphore(self.max_concurrent)
        log.info("Registered worker '%s' → %s", worker_type, worker_cls.__name__)

    def worker(self, worker_type: str):
        """Decorator: @dispatcher.worker('review')"""
        def decorator(cls: Type[SamvitWorker]):
            self.register(worker_type, cls)
            return cls
        return decorator

    async def run(self) -> None:
        """Main dispatch loop."""
        if not self._workers:
            raise RuntimeError("No workers registered — call dispatcher.register() first")

        await self._wait_for_samvit()
        token = await self._get_token()
        log.info("Dispatcher started. Watching types: %s", list(self._workers))

        async with httpx.AsyncClient(timeout=10) as client:
            while self._running:
                claimed_any = False
                for worker_type, worker_cls in self._workers.items():
                    sem = self._semaphores[worker_type]
                    if sem._value == 0:
                        continue  # at capacity for this type, skip
                    task = await self._claim(client, token, worker_type)
                    if not task:
                        continue
                    claimed_any = True
                    asyncio.create_task(
                        self._dispatch(task, worker_type, worker_cls, sem)
                    )
                if not claimed_any:
                    await asyncio.sleep(self.poll_interval)

    async def _dispatch(
        self,
        task: dict,
        worker_type: str,
        worker_cls: Type[SamvitWorker],
        sem: asyncio.Semaphore,
    ) -> None:
        """Instantiate worker, run task, release semaphore."""
        async with sem:
            worker = worker_cls(
                handle=self.handle,
                provider=self.provider,
                samvit_url=self.samvit_url,
            )
            try:
                await worker.startup()
                context = await worker._load_context(task)
                result  = await worker.execute(task, context=context)
                await worker._done(task["id"], task["claim_token"],
                                   result=result, status="done")
            except Exception as exc:
                log.error("Worker %s failed on task %s: %s",
                          worker_type, task["id"], exc)
                await worker._done(task["id"], task["claim_token"],
                                   result={"error": str(exc)}, status="failed")
            finally:
                await worker.shutdown()

    async def _claim(
        self,
        client: httpx.AsyncClient,
        token: str,
        worker_type: str,
    ) -> dict | None:
        try:
            r = await client.post(
                f"{self.samvit_url}/v1/tools/call",
                json={"tool": "claim", "params": {"tags": [worker_type]}},
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return r.json().get("task")
        except Exception as exc:
            log.debug("Dispatcher claim error: %s", exc)
        return None

    async def _get_token(self) -> str:
        """Re-use harness credential loading logic."""
        w = SamvitWorker(self.handle, self.provider, self.samvit_url)
        token, _ = await w._load_or_register()
        return token

    async def _wait_for_samvit(self) -> None:
        async with httpx.AsyncClient(timeout=5) as c:
            for attempt in range(1, STARTUP_RETRIES + 1):
                try:
                    r = await c.get(f"{self.samvit_url}/health")
                    if r.status_code == 200:
                        return
                except Exception:
                    pass
                log.warning("Samvit not ready (%d/%d)…", attempt, STARTUP_RETRIES)
                await asyncio.sleep(STARTUP_BACKOFF)
        raise RuntimeError("Samvit unreachable")
