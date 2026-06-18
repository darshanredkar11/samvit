from pathlib import Path

import pytest

from samvit import codegraph


def test_code_roots_default_to_workspace(monkeypatch):
    monkeypatch.delenv("SAMVIT_CODE_ROOTS", raising=False)
    assert codegraph._allowed_code_roots() == [Path("/workspace")]


def test_path_boundary_check():
    assert codegraph._is_within(Path("/workspace/repo"), Path("/workspace"))
    assert not codegraph._is_within(Path("/etc"), Path("/workspace"))
