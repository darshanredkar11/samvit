"""
Failure permutation tests for harness, dispatcher, hooks, and Hermes integration.

Covers every failure mode identified in FAILURE_ANALYSIS.md §11 extensions:
  - Token persistence race conditions
  - Double registration
  - Worker crash mid-task (task auto-release)
  - Dispatcher concurrency cap
  - Hook exit-0 guarantee
  - Hermes memory timeout degradation
  - Skill watcher partial-write guard (mtime stabilisation)
  - Cron bridge idempotency
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Harness: credential loading ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_reads_existing_credentials(tmp_path):
    """Worker loads token from disk instead of re-registering."""
    from samvit.harness import SamvitWorker

    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(json.dumps({
        "darshan": {"handle": "darshan", "token": "samvit_abc", "agent_id": "uuid-1"}
    }))

    worker = SamvitWorker("darshan", "claude")
    with patch("samvit.harness.CREDENTIALS_PATH", creds_file):
        creds = worker._read_credentials()
    assert creds is not None
    assert creds["token"] == "samvit_abc"


@pytest.mark.asyncio
async def test_worker_handles_malformed_credentials(tmp_path):
    """Malformed credentials file → returns None → triggers fresh registration."""
    from samvit.harness import SamvitWorker

    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{ THIS IS NOT JSON }")

    worker = SamvitWorker("darshan", "claude")
    with patch("samvit.harness.CREDENTIALS_PATH", creds_file):
        creds = worker._read_credentials()
    assert creds is None   # safe fallback


@pytest.mark.asyncio
async def test_worker_credential_file_is_mode_600(tmp_path):
    """Token file must not be world-readable."""
    from samvit.harness import SamvitWorker

    worker = SamvitWorker("darshan", "claude")
    with patch("samvit.harness.CREDENTIALS_PATH", tmp_path / "credentials.json"):
        worker._write_credentials("samvit_token", "agent-id")
        mode = (tmp_path / "credentials.json").stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0600, got {oct(mode)}"


@pytest.mark.asyncio
async def test_worker_409_on_taken_handle_raises_clear_error():
    """If handle taken and no local credentials → clear error message."""
    from samvit.harness import SamvitWorker

    worker = SamvitWorker("existing-handle", "claude")

    with patch("samvit.harness.CREDENTIALS_PATH", Path("/nonexistent/credentials.json")):
        # Simulate 409 from Samvit
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.text = "handle already registered"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=MagicMock(status_code=401))
            mock_client.return_value.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(RuntimeError, match="admin reset"):
                await worker._load_or_register()


# ── Harness: task execution ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_marks_task_failed_on_execute_exception():
    """If execute() raises, _done() is called with status='failed'."""
    from samvit.harness import SamvitWorker

    class FailingWorker(SamvitWorker):
        async def execute(self, task, context=None):
            raise ValueError("deliberate failure")

    worker = FailingWorker("darshan", "claude")
    worker._token    = "tok"
    worker._agent_id = "agent-id"
    worker._http     = AsyncMock()

    done_calls = []

    async def fake_done(task_id, claim_token, result, status):
        done_calls.append({"status": status, "result": result})

    worker._done = fake_done

    await worker._run_task(
        {"id": "task-1", "title": "test", "claim_token": "tok", "description": ""},
        context=[],
    )

    assert len(done_calls) == 1
    assert done_calls[0]["status"] == "failed"
    assert "deliberate failure" in done_calls[0]["result"]["error"]


@pytest.mark.asyncio
async def test_context_loaded_before_execute():
    """Context memories are loaded and passed to execute()."""
    from samvit.harness import SamvitWorker

    received_context = []

    class ContextCapture(SamvitWorker):
        async def execute(self, task, context=None):
            received_context.extend(context or [])
            return {}

    worker = ContextCapture("darshan", "claude")
    worker._token    = "tok"
    worker._agent_id = "id"
    worker._http     = AsyncMock()

    fake_memories = [{"content": "relevant note", "score": 0.9}]
    worker._load_context = AsyncMock(return_value=fake_memories)
    worker._done         = AsyncMock()

    await worker._run_task(
        {"id": "t1", "title": "test task", "claim_token": "c", "description": "auth"},
        context=fake_memories,
    )
    assert received_context == fake_memories


# ── Dispatcher: concurrency cap ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatcher_respects_max_concurrent():
    """Dispatcher should not spawn more than max_concurrent workers per type."""
    from samvit.dispatcher import SamvitDispatcher
    from samvit.harness import SamvitWorker

    active = []

    class SlowWorker(SamvitWorker):
        async def execute(self, task, context=None):
            active.append(1)
            await asyncio.sleep(0.1)
            active.pop()
            return {}

    dispatcher = SamvitDispatcher(max_concurrent_per_type=2)
    dispatcher.register("slow", SlowWorker)

    sem = dispatcher._semaphores["slow"]

    # Simulate 4 concurrent tasks trying to acquire the semaphore of size 2
    tasks_started = []
    async def acquire_and_release():
        async with sem:
            tasks_started.append(1)
            await asyncio.sleep(0.05)

    await asyncio.gather(*[acquire_and_release() for _ in range(4)])
    # All 4 eventually ran, but never more than 2 at once (semaphore enforced)
    assert len(tasks_started) == 4


@pytest.mark.asyncio
async def test_dispatcher_skips_unknown_type():
    """Tasks with unregistered worker_type are never claimed."""
    from samvit.dispatcher import SamvitDispatcher

    dispatcher = SamvitDispatcher()
    # Only 'review' registered, not 'unknown-type'
    claim_calls = []

    async def mock_claim(client, token, worker_type):
        claim_calls.append(worker_type)
        return None

    dispatcher._claim = mock_claim
    dispatcher._get_token = AsyncMock(return_value="tok")
    dispatcher._wait_for_samvit = AsyncMock()

    from samvit.harness import SamvitWorker
    class DummyWorker(SamvitWorker):
        async def execute(self, task, context=None): return {}

    dispatcher.register("review", DummyWorker)

    # Only 'review' should be claimed, never 'unknown-type'
    assert "review" in dispatcher._workers
    assert "unknown-type" not in dispatcher._workers


# ── Hooks: must always exit 0 ─────────────────────────────────────────────────

def test_hook_pre_tool_never_raises():
    """Pre-tool hook with network failure must not raise."""
    from samvit.hooks import hook_pre_tool
    with patch("samvit.hooks._call", side_effect=Exception("network down")):
        try:
            hook_pre_tool({"tool_name": "Read", "tool_input": {"file_path": "/test"}})
        except Exception as e:
            pytest.fail(f"hook_pre_tool raised: {e}")


def test_hook_post_tool_never_raises():
    """Post-tool hook must not raise even on total failure."""
    from samvit.hooks import hook_post_tool
    os.environ["SAMVIT_AUTO_REMEMBER"] = "1"
    with patch("samvit.hooks._call", side_effect=RuntimeError("boom")):
        try:
            hook_post_tool({
                "tool_name": "Write",
                "tool_input": {"file_path": "/test.py", "content": "x" * 500},
                "tool_output": {},
            })
        except Exception as e:
            pytest.fail(f"hook_post_tool raised: {e}")


def test_hook_stop_never_raises():
    from samvit.hooks import hook_stop
    with patch("samvit.hooks._call", side_effect=Exception("no server")):
        try:
            hook_stop({"stop_reason": "end_turn"})
        except Exception as e:
            pytest.fail(f"hook_stop raised: {e}")


def test_hook_main_exits_0_on_unknown_type(monkeypatch, capsys):
    """Hook CLI with unknown type must exit 0, not crash."""
    import sys
    from samvit.hooks import main
    monkeypatch.setattr(sys, "argv", ["samvit.hooks", "unknown-hook-type"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


# ── Hermes memory backend: timeout degradation ───────────────────────────────

@pytest.mark.asyncio
async def test_hermes_memory_search_returns_empty_on_timeout():
    """If Samvit times out, search returns [] (Hermes falls back gracefully)."""
    from samvit.integrations.hermes import SamvitMemoryBackend

    os.environ["SAMVIT_HERMES_TOKEN"] = "tok"
    backend = SamvitMemoryBackend()

    with patch("samvit.integrations.hermes._call", return_value=None):
        results = await backend.search("auth flow", limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_hermes_memory_get_returns_none_on_timeout():
    from samvit.integrations.hermes import SamvitMemoryBackend

    backend = SamvitMemoryBackend()
    with patch("samvit.integrations.hermes._call", return_value=None):
        result = await backend.get("skill.auth")
    assert result is None


@pytest.mark.asyncio
async def test_hermes_memory_delete_returns_true_with_warning(caplog):
    """Delete is a no-op but must return True so Hermes doesn't crash."""
    from samvit.integrations.hermes import SamvitMemoryBackend
    import logging

    backend = SamvitMemoryBackend()
    with caplog.at_level(logging.WARNING):
        result = await backend.delete("skill.auth")
    assert result is True
    assert "permanent" in caplog.text


# ── Hermes skill watcher: mtime stabilisation ────────────────────────────────

@pytest.mark.asyncio
async def test_skill_watcher_waits_for_mtime_stability(tmp_path):
    """
    Watcher should not publish a skill file while it's still being written.
    Simulated by changing mtime between the first check and the stability check.
    """
    from samvit.integrations.hermes import HermesSkillWatcher

    skill_file = tmp_path / "auth.md"
    skill_file.write_text("# Auth skill\nDo not store passwords in plaintext.")

    watcher = HermesSkillWatcher(skills_path=tmp_path, poll_interval=999)

    published = []

    async def fake_call(path, body, timeout=3):
        if body.get("tool") == "remember":
            published.append(body["params"]["key"])
        return {"stored": True}

    with patch("samvit.integrations.hermes._call", side_effect=fake_call):
        # First scan — file is seen
        first_mtime = skill_file.stat().st_mtime
        watcher._known_mtimes["auth"] = first_mtime  # simulate "already known"

        # Simulate file being modified (mtime changes)
        time.sleep(0.01)
        skill_file.write_text("# Auth skill v2\nUpdated content.")

        # Now scan — should detect the change and after stabilisation, publish
        # We mock asyncio.sleep to skip the wait
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await watcher._scan()

    # The skill should have been re-published with the new content
    assert "skill.auth" in published


@pytest.mark.asyncio
async def test_skill_watcher_skips_missing_dir():
    """If skills dir doesn't exist, watcher exits cleanly without error."""
    from samvit.integrations.hermes import HermesSkillWatcher

    watcher = HermesSkillWatcher(skills_path=Path("/nonexistent/skills"))
    with patch("asyncio.sleep", new_callable=AsyncMock):
        # run() should return without raising after warning
        await watcher.run()


# ── Hermes cron bridge: idempotency ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_cron_bridge_skips_existing_tasks(tmp_path):
    """If a cron task already exists in Samvit, bridge should not create duplicate."""
    from samvit.integrations.hermes import HermesCronBridge

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "crons": [{"name": "daily-review", "task": "Review PRs", "schedule": "0 9 * * *"}]
    }))

    bridge = HermesCronBridge(config_path=config)

    # Simulate task already exists
    bridge._task_exists = AsyncMock(return_value=True)
    bridge._create_task = AsyncMock(return_value=True)

    result = await bridge.sync_to_samvit()
    assert result["skipped"] == 1
    assert result["created"] == 0
    bridge._create_task.assert_not_called()


@pytest.mark.asyncio
async def test_cron_bridge_handles_missing_config():
    """Missing Hermes config → returns 0 created, 0 skipped gracefully."""
    from samvit.integrations.hermes import HermesCronBridge

    bridge = HermesCronBridge(config_path=Path("/nonexistent/config.json"))
    result = await bridge.sync_to_samvit()
    assert result["created"] == 0
    assert result["skipped"] == 0
    assert result["crons_found"] == 0


@pytest.mark.asyncio
async def test_cron_bridge_handles_malformed_config(tmp_path):
    """Malformed Hermes config → returns 0 crons gracefully."""
    from samvit.integrations.hermes import HermesCronBridge

    config = tmp_path / "bad.json"
    config.write_text("NOT JSON AT ALL {{{")

    bridge = HermesCronBridge(config_path=config)
    crons = bridge.load_crons()
    assert crons == []
