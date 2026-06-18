"""E2E: code graph — index repo → explore_code → who_calls."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path

import pytest

from samvit import codegraph


@pytest.mark.asyncio
async def test_index_repo_and_explore(agent_rec, monkeypatch):
    """Create a small repo, index it, search for symbols."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("SAMVIT_CODE_ROOTS", tmpdir)

        repo_root = Path(tmpdir) / "test-repo"
        repo_root.mkdir()

        (repo_root / "utils.py").write_text("""
def log_message(msg: str) -> None:
    '''Log a message to stdout.'''
    print(msg)

def process_data(data: list) -> list:
    '''Transform input data by doubling each element.'''
    log_message(f"Processing {len(data)} items")
    return [x * 2 for x in data]
""")

        (repo_root / "main.py").write_text("""
from utils import process_data, log_message

def start() -> None:
    '''Entry point: start the application.'''
    log_message("Starting app")
    result = process_data([1, 2, 3])
    print(f"Result: {result}")

if __name__ == "__main__":
    start()
""")

        result = await codegraph.index_repo(
            agent_rec,
            root_path=str(repo_root),
            repo_id="e2e-test-repo",
        )
        assert result["nodes"] >= 3
        assert result["files"] >= 2

        explored = await codegraph.explore_code(
            agent_rec,
            repo_id="e2e-test-repo",
            query="data processing transformation",
            limit=5,
        )
        assert len(explored["results"]) > 0
        names = [r["name"] for r in explored["results"]]
        assert any("process" in n.lower() for n in names)


@pytest.mark.asyncio
async def test_who_calls_function(agent_rec, monkeypatch):
    """Index repo, then find callers of a function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("SAMVIT_CODE_ROOTS", tmpdir)

        repo_root = Path(tmpdir) / "caller-test"
        repo_root.mkdir()

        (repo_root / "greeter.py").write_text("""
def greet(name: str) -> str:
    '''Return a greeting for the given name.'''
    return f"Hello, {name}!"

def announce(users: list) -> None:
    '''Greet all users in the list.'''
    for user in users:
        msg = greet(user)
        print(msg)
""")

        await codegraph.index_repo(
            agent_rec,
            root_path=str(repo_root),
            repo_id="caller-e2e",
        )

        callers = await codegraph.who_calls(
            agent_rec,
            repo_id="caller-e2e",
            function_name="greet",
        )
        assert len(callers["callers"]) > 0
        caller_names = [c["name"] for c in callers["callers"]]
        assert "announce" in caller_names
