"""
Samvit ↔ Hermes Agent integration — three bridge points.

1. SamvitMemoryBackend
   Implements Hermes's external memory plugin interface.
   Hermes agents on any machine share one skill pool via Samvit.
   Mount at GET/POST /v1/hermes/memory/* in main.py.

2. HermesCronBridge
   Reads ~/.hermes/config.json cron definitions.
   Creates Samvit tasks instead — dynamic, prioritised, no double-execution.
   Run once at startup or as a scheduled refresh.

3. HermesSkillWatcher
   Watches ~/.hermes/skills/ for new/modified .md skill files.
   Auto-publishes each skill to Samvit via remember(key="skill.{name}").
   Every agent on the team gets new skills immediately.
   Uses polling (asyncio) — no watchdog dep required.

Failure modes handled:
  - Hermes config not found → graceful degradation, log warning
  - Skill file partially written (OS fires event on open, before close)
    → we wait for file mtime to stabilise (1s) before reading
  - Duplicate skill publish → KV upsert is idempotent (ON CONFLICT DO UPDATE)
  - Cron task already in queue → check before insert, skip if pending/claimed
  - Memory request times out → Hermes falls back to local memory (its default)
  - Samvit unreachable → all methods return empty/None, Hermes degrades gracefully

Configuration (env vars or hermes config):
  SAMVIT_URL                — default http://localhost:8765
  SAMVIT_HERMES_TOKEN       — bearer token for the Hermes integration agent
  SAMVIT_HERMES_HANDLE      — handle used for Hermes agent (default "hermes")
  HERMES_CONFIG_PATH        — default ~/.hermes/config.json
  HERMES_SKILLS_PATH        — default ~/.hermes/skills/
  HERMES_SKILL_POLL_INTERVAL — seconds between skill directory scans (default 10)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

SAMVIT_URL      = os.environ.get("SAMVIT_URL", "http://localhost:8765")
HERMES_TOKEN    = os.environ.get("SAMVIT_HERMES_TOKEN", "")
HERMES_HANDLE   = os.environ.get("SAMVIT_HERMES_HANDLE", "hermes")
HERMES_CFG      = Path(os.environ.get("HERMES_CONFIG_PATH",
                                       Path.home() / ".hermes" / "config.json"))
HERMES_SKILLS   = Path(os.environ.get("HERMES_SKILLS_PATH",
                                       Path.home() / ".hermes" / "skills"))
SKILL_POLL_SEC  = float(os.environ.get("HERMES_SKILL_POLL_INTERVAL", "10"))
MEMORY_TIMEOUT  = float(os.environ.get("SAMVIT_HERMES_MEMORY_TIMEOUT", "3"))

SKILL_KEY_PREFIX = "skill."


def _headers() -> dict:
    return {"Authorization": f"Bearer {HERMES_TOKEN}"}


async def _call(path: str, body: dict, timeout: float = MEMORY_TIMEOUT) -> dict | None:
    if not HERMES_TOKEN:
        log.debug("SAMVIT_HERMES_TOKEN not set — Hermes integration inactive")
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{SAMVIT_URL}{path}", json=body, headers=_headers())
            if r.status_code == 200:
                return r.json()
            log.debug("Samvit returned %d for %s", r.status_code, path)
    except Exception as exc:
        log.debug("Samvit unreachable (%s) — Hermes will use local memory", exc)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. Memory Backend — implements Hermes external memory plugin interface
# ══════════════════════════════════════════════════════════════════════════════

class SamvitMemoryBackend:
    """
    Drop-in external memory backend for Hermes Agent.

    Hermes calls these methods instead of its local vector DB:
      store(content, metadata)   → samvit.remember()
      search(query, limit)       → samvit.recall()
      get(key)                   → samvit.recall(key=key)
      delete(key)                → no-op (Samvit memories are permanent)

    Mount the HTTP routes in main.py:
      GET  /v1/hermes/memory/search?q=...&limit=5
      POST /v1/hermes/memory/store
      GET  /v1/hermes/memory/get?key=...

    Hermes config (~/.hermes/config.json):
      {
        "memory": {
          "backend": "external",
          "url": "http://localhost:8765/v1/hermes/memory"
        }
      }
    """

    async def store(self, content: str, metadata: dict | None = None, key: str | None = None) -> bool:
        """Store a memory. Returns True on success."""
        result = await _call("/v1/tools/call", {
            "tool": "remember",
            "params": {
                "content": content,
                "key": key,
                "namespace": "global",
                "metadata": metadata or {},
            },
        })
        return result is not None and result.get("stored", False)

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Semantic search — returns list of {content, score, agent} dicts."""
        result = await _call("/v1/tools/call", {
            "tool": "recall",
            "params": {"query": query, "limit": limit, "namespace": "global"},
        })
        if result is None:
            return []
        return [
            {"content": r["content"], "score": r["score"], "agent": r["agent"],
             "metadata": r.get("metadata", {})}
            for r in result.get("results", [])
        ]

    async def get(self, key: str) -> dict | None:
        """Exact key lookup."""
        result = await _call("/v1/tools/call", {
            "tool": "recall",
            "params": {"key": key, "namespace": "global"},
        })
        if result is None:
            return None
        results = result.get("results", [])
        return results[0] if results else None

    async def delete(self, key: str) -> bool:
        """Delete a key from Samvit via the forget tool."""
        result = await _call("/v1/tools/call", {
            "tool": "forget",
            "params": {"key": key, "namespace": "global"},
        })
        if result is None:
            return False
        return result.get("deleted", False)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Cron Bridge — Hermes crons → Samvit task queue
# ══════════════════════════════════════════════════════════════════════════════

class HermesCronBridge:
    """
    Reads Hermes cron definitions and creates Samvit tasks instead.
    Hermes worker agents then claim() tasks dynamically — no fixed scheduling,
    no double-execution, priority-ordered, auto-releases on crash.

    Hermes cron format (from ~/.hermes/config.json):
      {
        "crons": [
          {"name": "daily-review",  "schedule": "0 9 * * *",  "task": "Review PR queue",  "priority": 2},
          {"name": "weekly-sync",   "schedule": "0 10 * * 1", "task": "Team sync notes",   "priority": 1},
          {"name": "code-health",   "schedule": "0 18 * * *", "task": "Run code health checks"}
        ]
      }
    """

    def __init__(self, config_path: Path = HERMES_CFG) -> None:
        self.config_path = config_path
        self._known_cron_names: set[str] = set()

    def load_crons(self) -> list[dict]:
        """Load cron definitions from Hermes config. Returns [] if not found."""
        if not self.config_path.exists():
            log.warning("Hermes config not found at %s — cron bridge inactive", self.config_path)
            return []
        try:
            data = json.loads(self.config_path.read_text())
            return data.get("crons", [])
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Could not read Hermes config: %s", exc)
            return []

    async def sync_to_samvit(self) -> dict:
        """
        For each cron definition, create a Samvit task if one isn't already
        pending or claimed. Also cancels tasks for crons that were removed
        from the config. Returns {created: N, skipped: N, cancelled: N}.
        Idempotent — safe to call repeatedly.
        """
        crons = self.load_crons()
        if not crons:
            return {"created": 0, "skipped": 0, "crons_found": 0, "cancelled": 0}

        current_names = {cron.get("name", "unnamed") for cron in crons}
        created = skipped = cancelled = 0

        for cron in crons:
            name     = cron.get("name", "unnamed")
            task_txt = cron.get("task", name)
            priority = int(cron.get("priority", 0))
            schedule = cron.get("schedule", "")

            # Check if a task with this cron name is already pending/claimed
            already_exists = await self._task_exists(name)
            if already_exists:
                skipped += 1
                continue

            # Use the HTTP task-create endpoint directly
            ok = await self._create_task(
                title=task_txt,
                description=f"Hermes cron: {name} | schedule: {schedule}",
                tags=["hermes-cron", name],
                priority=priority,
                worker_type="hermes",
            )
            if ok:
                created += 1
                log.info("Created Samvit task for Hermes cron: %s", name)
            else:
                log.warning("Failed to create task for cron: %s", name)

        # Clean up orphaned cron tasks (removed from config)
        removed_names = self._known_cron_names - current_names
        if removed_names:
            cancelled = await self._cancel_orphaned_tasks(removed_names)

        self._known_cron_names = current_names

        return {"created": created, "skipped": skipped, "cancelled": cancelled, "crons_found": len(crons)}

    async def _cancel_orphaned_tasks(self, cron_names: set[str]) -> int:
        """Cancel pending/claimed tasks for crons that no longer exist."""
        cancelled = 0
        for name in cron_names:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(
                        f"{SAMVIT_URL}/v1/hermes/task-exists",
                        params={"tag": name},
                        headers=_headers(),
                    )
                    if r.status_code != 200 or not r.json().get("exists", False):
                        continue
                # Cancel via the HTTP bridge — list tasks with this tag, then cancel each
                list_resp = await _call("/v1/tools/call", {
                    "tool": "list_tasks",
                    "params": {"tags": [name], "status": "pending", "limit": 50},
                })
                if list_resp:
                    for task in list_resp.get("tasks", []):
                        await _call("/v1/tools/call", {
                            "tool": "cancel_task",
                            "params": {"task_id": task["id"]},
                        })
                        cancelled += 1
                        log.info("Cancelled orphaned cron task: %s (%s)", name, task["id"])
            except Exception as exc:
                log.warning("Failed to cancel orphaned cron %s: %s", name, exc)
        return cancelled

    async def _task_exists(self, cron_name: str) -> bool:
        """Check if a task tagged with this cron name is already active."""
        if not HERMES_TOKEN:
            return False
        try:
            async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT) as c:
                r = await c.get(
                    f"{SAMVIT_URL}/v1/hermes/task-exists",
                    params={"tag": cron_name},
                    headers=_headers(),
                )
                if r.status_code == 200:
                    return r.json().get("exists", False)
        except Exception as exc:
            log.debug("task-exists check failed: %s", exc)
        return False

    async def _create_task(
        self,
        title: str,
        description: str,
        tags: list[str],
        priority: int,
        worker_type: str,
    ) -> bool:
        """Create a task via Samvit HTTP API."""
        if not HERMES_TOKEN:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(
                    f"{SAMVIT_URL}/v1/tasks",
                    json={
                        "title": title,
                        "description": description,
                        "tags": tags,
                        "priority": priority,
                        "worker_type": worker_type,
                    },
                    headers=_headers(),
                )
                if r.status_code in (200, 201):
                    return True
        except Exception as exc:
            log.error("Task creation failed: %s", exc)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 3. Skill Watcher — auto-publish new Hermes skills to Samvit
# ══════════════════════════════════════════════════════════════════════════════

class HermesSkillWatcher:
    """
    Polls ~/.hermes/skills/*.md and publishes new/changed skills to Samvit.

    When Hermes writes a new skill:
      1. Watcher detects the file (by mtime comparison)
      2. Waits 1s for file to finish writing (mtime stabilisation)
      3. Calls remember(content, key="skill.{name}") — KV upsert
      4. Broadcasts via say(topic="skills") so agents know a new skill is available

    Every agent on the team gets new Hermes skills immediately.
    Skills are searchable via recall("how to do X").

    Failure modes:
      - Skills dir not found → warn and skip
      - Partial file write → mtime stabilisation waits for write to complete
      - Duplicate publish → KV upsert is idempotent
      - Same skill re-published after edit → updated in Samvit (upsert)
    """

    def __init__(
        self,
        skills_path: Path = HERMES_SKILLS,
        poll_interval: float = SKILL_POLL_SEC,
    ) -> None:
        self.skills_path   = skills_path
        self.poll_interval = poll_interval
        self._known_mtimes: dict[str, float] = {}

    async def run(self) -> None:
        """Poll loop — run as an asyncio background task."""
        if not self.skills_path.exists():
            log.warning("Hermes skills directory not found at %s — skill watcher inactive",
                        self.skills_path)
            return

        log.info("Hermes skill watcher started: %s (poll=%.0fs)",
                 self.skills_path, self.poll_interval)

        while True:
            try:
                await self._scan()
            except Exception as exc:
                log.error("Skill watcher scan error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    async def _scan(self) -> None:
        for path in self.skills_path.glob("*.md"):
            name = path.stem
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue  # file deleted between glob and stat

            if self._known_mtimes.get(name) == mtime:
                continue  # unchanged

            # Wait 1s for file to finish writing (mtime stabilisation)
            await asyncio.sleep(1.0)
            try:
                new_mtime = path.stat().st_mtime
            except OSError:
                continue
            if new_mtime != mtime:
                continue  # still being written, catch next poll

            # File is stable — publish to Samvit
            try:
                content = path.read_text(errors="replace").strip()
            except OSError:
                continue

            if not content:
                continue

            key = f"{SKILL_KEY_PREFIX}{name}"
            is_new = name not in self._known_mtimes

            result = await _call("/v1/tools/call", {
                "tool": "remember",
                "params": {
                    "content": f"[Hermes Skill: {name}]\n\n{content}",
                    "key": key,
                    "namespace": "global",
                    "metadata": {"source": "hermes-skill", "skill_name": name},
                },
            })

            if result:
                self._known_mtimes[name] = new_mtime
                action = "Published new" if is_new else "Updated"
                log.info("%s Hermes skill: %s", action, name)

                # Broadcast so all agents know about the new skill
                await _call("/v1/tools/call", {
                    "tool": "say",
                    "params": {
                        "body": f"{'New' if is_new else 'Updated'} Hermes skill available: {name}. "
                                f"Use: recall --key {key}",
                        "topic": "skills",
                    },
                })

    async def publish_all(self) -> dict:
        """Force-publish all existing skills. Useful on first startup."""
        if not self.skills_path.exists():
            return {"published": 0, "error": "skills directory not found"}
        published = 0
        for path in self.skills_path.glob("*.md"):
            try:
                content = path.read_text(errors="replace").strip()
                name    = path.stem
                if not content:
                    continue
                result = await _call("/v1/tools/call", {
                    "tool": "remember",
                    "params": {
                        "content": f"[Hermes Skill: {name}]\n\n{content}",
                        "key":  f"{SKILL_KEY_PREFIX}{name}",
                        "namespace": "global",
                        "metadata": {"source": "hermes-skill", "skill_name": name},
                    },
                })
                if result:
                    self._known_mtimes[name] = path.stat().st_mtime
                    published += 1
            except Exception as exc:
                log.warning("Could not publish skill %s: %s", path.stem, exc)
        log.info("Bulk published %d Hermes skills", published)
        return {"published": published}
