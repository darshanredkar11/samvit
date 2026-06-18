"""Tests for DB retry and AcquireConn (Phase 1.4)."""
from __future__ import annotations
import asyncpg
import pytest
from samvit.db import _is_transient_error, AcquireConn


class FakeSerialError(asyncpg.exceptions.SerializationError):
    pass


class FakeDeadlockError(asyncpg.exceptions.DeadlockDetectedError):
    pass


class FakeConnectionError(ConnectionError):
    pass


class FakeOtherError(RuntimeError):
    pass


def test_transient_serialization():
    assert _is_transient_error(FakeSerialError()) is True


def test_transient_deadlock():
    assert _is_transient_error(FakeDeadlockError()) is True


def test_transient_connection_error():
    assert _is_transient_error(FakeConnectionError()) is True


def test_non_transient_raises():
    assert _is_transient_error(FakeOtherError()) is False


def test_transient_oserror():
    assert _is_transient_error(OSError("connection refused")) is True


@pytest.mark.asyncio
async def test_acquire_conn_is_context_manager():
    """AcquireConn must be usable as async context manager."""
    ac = AcquireConn()
    assert hasattr(ac, "__aenter__")
    assert hasattr(ac, "__aexit__")
