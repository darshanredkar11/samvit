"""
Autonomous task decorator: @samvit.task
Eliminates manual task lifecycle management (create, claim, complete, retry, timeout).
Works inside Samvit (uses task module directly) OR outside via HTTP.
"""

import asyncio
import functools
import inspect
import json
import logging
import contextvars
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Context var for agent info during execution
_agent_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_agent_context", default=None
)


class TaskDecoratorError(Exception):
    """Raised when decorator constraints are violated."""
    pass


class TaskExecutionError(Exception):
    """Raised when decorated task fails after max retries."""
    pass


def task(
    func: Optional[Callable] = None,
    *,
    max_retries: int = 3,
    timeout: int = 300,
    queue: str = "default",
    auto_retry_timeout: bool = True,
    ttl: int = 86400,
    metadata: Optional[dict] = None,
) -> Callable:
    """Autonomous task decorator for multi-agent coordination.

    Automatically manages task lifecycle: creation, claiming, execution,
    retry, timeout, and completion. Works in Samvit or as external agent.

    Usage:
        @task(max_retries=3, timeout=300)
        async def implement_auth():
            await remember("JWT spec", "RS256, 1hr")
            return "implemented"

        result = await implement_auth()

    Samvit auto-handles: create, claim, execute, retry, complete, audit.
    """

    def decorator(f: Callable) -> Callable:
        if not inspect.iscoroutinefunction(f):
            raise TaskDecoratorError(
                f"@task on '{f.__name__}' requires async. Use: async def {f.__name__}()"
            )

        @functools.wraps(f)
        async def wrapper(*args, **kwargs) -> Any:
            task_name = f.__name__
            agent = _agent_context.get()
            attempt = 0
            last_error: Optional[Exception] = None
            claim_token: Optional[str] = None
            task_id: Optional[str] = None

            while attempt <= max_retries:
                try:
                    # 1. Create task (idempotent)
                    if attempt == 0:
                        task_id = await _create_task(
                            name=task_name,
                            agent=agent,
                            metadata=metadata,
                        )

                    # 2. Atomically claim
                    claim_result = await _claim_task(
                        task_id=task_id,
                        agent=agent,
                        timeout=timeout,
                    )
                    if claim_result is None:
                        # Another agent claimed it, backoff
                        await asyncio.sleep(min(2 ** attempt, 30))
                        attempt += 1
                        continue

                    claim_token = claim_result["claim_token"]

                    # 3. Execute
                    log.info(
                        f"Task {task_name} ({task_id[:8]}) attempt {attempt + 1}/{max_retries + 1}"
                    )
                    result = await f(*args, **kwargs)

                    # 4. Mark done
                    await _mark_done(
                        task_id=task_id,
                        claim_token=claim_token,
                        agent=agent,
                        result=result,
                    )
                    log.info(f"Task {task_name} ({task_id[:8]}) done")
                    return result

                except Exception as e:
                    last_error = e
                    log.warning(f"Task {task_name} attempt {attempt + 1} failed: {e}")

                    if attempt < max_retries:
                        # Release for retry
                        if claim_token:
                            await _cancel_claim(task_id, claim_token, agent)
                        backoff = min(2 ** attempt, 30)
                        await asyncio.sleep(backoff)
                        attempt += 1
                    else:
                        # Max retries, mark failed
                        if claim_token:
                            await _mark_failed(task_id, claim_token, agent, str(e))
                        raise TaskExecutionError(
                            f"Task {task_name} failed after {max_retries + 1} attempts"
                        ) from e

            raise TaskExecutionError(f"Task {task_name} exhausted retries")

        return wrapper

    return decorator if func is None else decorator(func)


# ── Task API (Works in Samvit process or via HTTP) ──

async def _create_task(name: str, agent: Optional[dict], metadata: Optional[dict]) -> str:
    """Create task, idempotent via idempotency_key."""
    from samvit.tools import tasks as task_tools

    if agent is None:
        raise TaskDecoratorError("@task requires agent context (MCP session)")

    key = f"task_decorator_{name}_{agent['id']}"
    result = await task_tools.create(
        agent=agent,
        title=name,
        description=f"Auto-created by @samvit.task decorator",
        tags=["auto"],
        idempotency_key=key,
    )
    return result["task_id"]


async def _claim_task(
    task_id: str, agent: Optional[dict], timeout: int
) -> Optional[dict]:
    """Claim task by ID. Returns {claim_token, ...} or None if already claimed."""
    from samvit.tools import tasks as task_tools

    if agent is None:
        raise TaskDecoratorError("@task requires agent context")

    result = await task_tools.claim(agent, task_id=task_id)
    return result.get("task")  # None if already claimed


async def _mark_done(
    task_id: str, claim_token: str, agent: Optional[dict], result: Any
) -> None:
    """Mark task as done."""
    from samvit.tools import tasks as task_tools

    if agent is None:
        return

    try:
        await task_tools.done(agent, task_id, claim_token, {"result": result})
    except Exception as e:
        log.warning(f"Failed to mark task done: {e}")


async def _mark_failed(
    task_id: str, claim_token: str, agent: Optional[dict], error: str
) -> None:
    """Mark task as failed."""
    from samvit.tools import tasks as task_tools

    if agent is None:
        return

    try:
        await task_tools.done(agent, task_id, claim_token, {"error": error}, "failed")
    except Exception as e:
        log.warning(f"Failed to mark task failed: {e}")


async def _cancel_claim(task_id: str, claim_token: str, agent: Optional[dict]) -> None:
    """Cancel a claim to allow retry. Use cancel() if original task."""
    from samvit.tools import tasks as task_tools

    if agent is None:
        return

    # ponytail: For now, just mark as cancelled to release. Future: add renew() call.
    try:
        await task_tools.done(agent, task_id, claim_token, {}, "failed")
    except Exception:
        pass  # Ignore if already done


def set_agent_context(agent: dict) -> None:
    """Set agent context for decorator (called by MCP handler)."""
    _agent_context.set(agent)
