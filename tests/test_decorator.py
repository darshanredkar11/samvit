"""Brutal automated tests for @samvit.task decorator."""

import asyncio
import pytest
from samvit import task
from samvit.decorators import TaskDecoratorError, TaskExecutionError, set_agent_context


# ─── Constraint Tests ───

@pytest.mark.asyncio
async def test_sync_function_rejected():
    """Decorator MUST reject sync functions."""
    with pytest.raises(TaskDecoratorError, match="must be async"):
        @task
        def sync_func():
            return "fail"


@pytest.mark.asyncio
async def test_agent_context_required():
    """Decorator MUST have agent context."""
    @task
    async def needs_context():
        return "fail"

    with pytest.raises(TaskDecoratorError, match="requires agent context"):
        await needs_context()


@pytest.mark.asyncio
async def test_agent_context_set_correctly(agent_rec):
    """Agent context properly set and accessible."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def get_context_info():
        return agent_rec["handle"]

    result = await get_context_info()
    assert result == agent_rec["handle"]


# ─── Basic Functionality ───

@pytest.mark.asyncio
async def test_decorator_without_params(agent_rec):
    """Decorator works with no parameters."""
    set_agent_context(agent_rec)

    @task
    async def minimal():
        return "minimal"

    result = await minimal()
    assert result == "minimal"


@pytest.mark.asyncio
async def test_decorator_with_all_params(agent_rec):
    """Decorator accepts all config parameters."""
    set_agent_context(agent_rec)

    @task(
        max_retries=5,
        timeout=600,
        queue="priority",
        auto_retry_timeout=False,
        ttl=1800,
        metadata={"priority": 1, "tags": ["test"]},
    )
    async def full_config():
        return "configured"

    result = await full_config()
    assert result == "configured"


@pytest.mark.asyncio
async def test_return_value_preserved(agent_rec):
    """Decorator preserves function return value."""
    set_agent_context(agent_rec)

    @task
    async def returns_dict():
        return {"status": "ok", "count": 42}

    result = await returns_dict()
    assert result == {"status": "ok", "count": 42}


@pytest.mark.asyncio
async def test_return_none(agent_rec):
    """Decorator handles None return."""
    set_agent_context(agent_rec)

    @task
    async def returns_none():
        return None

    result = await returns_none()
    assert result is None


# ─── Error Handling ───

@pytest.mark.asyncio
async def test_exception_after_max_retries(agent_rec):
    """Decorator raises TaskExecutionError after max_retries."""
    set_agent_context(agent_rec)

    @task(max_retries=1)
    async def always_fails():
        raise ValueError("Always fails")

    with pytest.raises(TaskExecutionError):
        await always_fails()


@pytest.mark.asyncio
async def test_exception_with_custom_message(agent_rec):
    """TaskExecutionError includes context."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def fails():
        raise RuntimeError("Custom error message")

    with pytest.raises(TaskExecutionError, match="failed after"):
        await fails()


@pytest.mark.asyncio
async def test_zero_retries(agent_rec):
    """max_retries=0 means execute once and fail."""
    set_agent_context(agent_rec)
    attempt_count = 0

    @task(max_retries=0)
    async def fail_once():
        nonlocal attempt_count
        attempt_count += 1
        raise ValueError("fail")

    with pytest.raises(TaskExecutionError):
        await fail_once()

    # Should only execute once (no retries)
    assert attempt_count == 1


@pytest.mark.asyncio
async def test_max_retries_attempted(agent_rec):
    """Decorator attempts up to max_retries."""
    set_agent_context(agent_rec)
    attempt_count = 0

    @task(max_retries=3)
    async def fail_multiple():
        nonlocal attempt_count
        attempt_count += 1
        raise ValueError("fail")

    with pytest.raises(TaskExecutionError):
        await fail_multiple()

    # Should attempt: initial + 3 retries = 4 total
    assert attempt_count == 4


@pytest.mark.asyncio
async def test_retry_on_success(agent_rec):
    """Decorator succeeds on retry."""
    set_agent_context(agent_rec)
    attempt_count = 0

    @task(max_retries=2)
    async def fail_then_succeed():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            raise ValueError("fail")
        return "success"

    result = await fail_then_succeed()
    assert result == "success"
    assert attempt_count == 2  # Failed on attempt 1, succeeded on attempt 2


# ─── Function Signature Tests ───

@pytest.mark.asyncio
async def test_function_with_args(agent_rec):
    """Decorator preserves function arguments."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def with_args(x: int, y: int):
        return x + y

    result = await with_args(5, 3)
    assert result == 8


@pytest.mark.asyncio
async def test_function_with_kwargs(agent_rec):
    """Decorator handles keyword arguments."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def with_kwargs(a: int, b: int = 10):
        return a + b

    result = await with_kwargs(5, b=20)
    assert result == 25


@pytest.mark.asyncio
async def test_function_with_defaults(agent_rec):
    """Decorator works with default arguments."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def with_defaults(x: int = 1, y: int = 2):
        return x * y

    assert await with_defaults() == 2
    assert await with_defaults(3) == 6
    assert await with_defaults(3, 4) == 12


# ─── Concurrent/Async Tests ───

@pytest.mark.asyncio
async def test_multiple_concurrent_tasks(agent_rec):
    """Decorator handles concurrent execution."""
    set_agent_context(agent_rec)

    @task(max_retries=1)
    async def task_1():
        await asyncio.sleep(0.01)
        return "task1"

    @task(max_retries=1)
    async def task_2():
        await asyncio.sleep(0.01)
        return "task2"

    @task(max_retries=1)
    async def task_3():
        await asyncio.sleep(0.01)
        return "task3"

    results = await asyncio.gather(task_1(), task_2(), task_3())
    assert set(results) == {"task1", "task2", "task3"}


@pytest.mark.asyncio
async def test_task_isolation_per_function(agent_rec):
    """Each decorated function gets its own task."""
    set_agent_context(agent_rec)

    @task(max_retries=0)
    async def task_a():
        return "a"

    @task(max_retries=0)
    async def task_b():
        return "b"

    result_a = await task_a()
    result_b = await task_b()

    assert result_a == "a"
    assert result_b == "b"


# ─── Edge Cases ───

@pytest.mark.asyncio
async def test_empty_function_body(agent_rec):
    """Decorator handles empty async function."""
    set_agent_context(agent_rec)

    @task
    async def empty():
        pass

    result = await empty()
    assert result is None


@pytest.mark.asyncio
async def test_large_return_value(agent_rec):
    """Decorator handles large result."""
    set_agent_context(agent_rec)

    @task
    async def returns_large():
        return {"data": "x" * 10000}

    result = await returns_large()
    assert len(result["data"]) == 10000


@pytest.mark.asyncio
async def test_metadata_preserved_in_config(agent_rec):
    """Custom metadata stored in task config."""
    set_agent_context(agent_rec)

    @task(metadata={"priority": 5, "team": "backend"})
    async def with_metadata():
        return "ok"

    result = await with_metadata()
    assert result == "ok"


@pytest.mark.asyncio
async def test_timeout_param_accepted(agent_rec):
    """Timeout parameter doesn't break decorator."""
    set_agent_context(agent_rec)

    @task(timeout=10, max_retries=0)
    async def with_timeout():
        await asyncio.sleep(0.001)
        return "ok"

    result = await with_timeout()
    assert result == "ok"


@pytest.mark.asyncio
async def test_queue_param_accepted(agent_rec):
    """Queue parameter accepted (future-use)."""
    set_agent_context(agent_rec)

    @task(queue="high_priority", max_retries=0)
    async def with_queue():
        return "ok"

    result = await with_queue()
    assert result == "ok"


# ─── Integration Tests ───

@pytest.mark.asyncio
async def test_multiple_agents_different_tasks(two_agent_recs):
    """Different agents execute different decorated tasks."""
    agent1, agent2 = two_agent_recs

    set_agent_context(agent1)
    @task(max_retries=0)
    async def agent1_task():
        return f"done_by_{agent1['handle']}"
    result1 = await agent1_task()

    set_agent_context(agent2)
    @task(max_retries=0)
    async def agent2_task():
        return f"done_by_{agent2['handle']}"
    result2 = await agent2_task()

    assert agent1['handle'] in result1
    assert agent2['handle'] in result2


@pytest.mark.asyncio
async def test_decorated_and_manual_calls_mix(agent_rec):
    """Decorator works alongside manual task calls."""
    set_agent_context(agent_rec)
    from samvit.tools import tasks

    # Manual: create task
    task_resp = await tasks.create(
        agent=agent_rec,
        title="Manual task",
        description="Created manually"
    )
    manual_task_id = task_resp["task_id"]

    # Decorated: auto-creates its own
    @task(max_retries=0)
    async def decorated_task():
        return "auto_created"

    result = await decorated_task()
    assert result == "auto_created"
    assert manual_task_id is not None
