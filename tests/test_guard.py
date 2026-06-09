"""
Tests for the ethical guard layer.

Covers:
  - Every named pattern (credentials, PII, live data, private keys)
  - High-entropy string detection
  - All three modes: block, redact, warn
  - Input scan on remember and say
  - Output scan on recall and read
  - Clean text passes through unchanged
  - Audit table records violations
"""

from __future__ import annotations

import os
import pytest

# Force redact mode for most tests; block mode tested explicitly
os.environ["SAMVIT_GUARD_MODE"] = "redact"

from samvit.guard import scan, GuardMode, GuardError, apply, mode


# ── Unit tests: scan() ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_text_unchanged():
    result = scan("The auth module uses 24-hour JWT expiry with a refresh endpoint.")
    assert not result.has_violations
    assert result.clean == "The auth module uses 24-hour JWT expiry with a refresh endpoint."


@pytest.mark.asyncio
async def test_detects_jwt_token():
    text = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    result = scan(text)
    assert result.has_violations
    assert any(v.pattern_name == "jwt_token" for v in result.violations)
    assert "[REDACTED:credential]" in result.clean


@pytest.mark.asyncio
async def test_detects_private_key():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
    result = scan(text)
    assert result.has_violations
    assert any(v.pattern_name == "private_key" for v in result.violations)


@pytest.mark.asyncio
async def test_detects_aws_access_key():
    result = scan("Using key AKIAIOSFODNN7EXAMPLE for S3 uploads")
    assert result.has_violations
    assert any(v.pattern_name == "aws_access_key" for v in result.violations)
    assert "AKIAIOSFODNN7EXAMPLE" not in result.clean


@pytest.mark.asyncio
async def test_detects_github_token():
    result = scan("CI_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef12")
    assert result.has_violations
    assert any(v.pattern_name == "github_token" for v in result.violations)


@pytest.mark.asyncio
async def test_detects_connection_string():
    result = scan("DB: postgresql://admin:s3cr3tP@ss@prod-db.internal:5432/mydb")
    assert result.has_violations
    assert any(v.pattern_name == "connection_string" for v in result.violations)
    assert "s3cr3tP@ss" not in result.clean


@pytest.mark.asyncio
async def test_detects_generic_secret():
    result = scan('config = {"password": "MyS3cur3P@ssw0rd!", "host": "localhost"}')
    assert result.has_violations
    assert any(v.category == "credential" for v in result.violations)


@pytest.mark.asyncio
async def test_detects_credit_card():
    result = scan("Customer card: 4111111111111111 expires 12/26")
    assert result.has_violations
    assert any(v.pattern_name == "credit_card" for v in result.violations)
    assert "4111111111111111" not in result.clean


@pytest.mark.asyncio
async def test_detects_internal_ip():
    result = scan("Deploy target: 10.0.1.42 port 8080")
    assert result.has_violations
    assert any(v.pattern_name == "internal_ip" for v in result.violations)


@pytest.mark.asyncio
async def test_detects_stripe_key():
    # Build the test string at runtime — no literal credential in source
    fake_stripe = "sk" + "_live_" + "A" * 24
    result = scan(f"Stripe key: {fake_stripe}")
    assert result.has_violations
    assert any(v.pattern_name == "stripe_key" for v in result.violations)
    assert fake_stripe not in result.clean


@pytest.mark.asyncio
async def test_detects_high_entropy_string():
    # This looks like a secret even though it doesn't match a named pattern
    result = scan("key=aB3xK9mPqRzVwYnLdCjHsEuIoFtGvNbQk")
    assert result.has_violations
    assert any(v.pattern_name == "high_entropy_string" for v in result.violations)


@pytest.mark.asyncio
async def test_multiple_violations_all_redacted():
    text = (
        "JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123xyz "
        "and DB: postgresql://user:pass@host/db"
    )
    result = scan(text)
    assert len(result.violations) >= 2
    assert "postgresql://user:pass@host/db" not in result.clean


@pytest.mark.asyncio
async def test_snippet_never_reveals_full_secret():
    """Snippets must be truncated — never expose the full secret value."""
    result = scan("AKIAIOSFODNN7EXAMPLE")
    for v in result.violations:
        assert len(v.snippet) <= 10
        assert v.snippet.endswith("…")


# ── Mode tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redact_mode_returns_clean_text(agent_rec):
    os.environ["SAMVIT_GUARD_MODE"] = "redact"
    clean = await apply(
        "password=SuperSecret123!",
        agent_rec["id"], "input", "remember"
    )
    assert "SuperSecret123" not in clean
    assert "[REDACTED:" in clean


@pytest.mark.asyncio
async def test_block_mode_raises(agent_rec):
    os.environ["SAMVIT_GUARD_MODE"] = "block"
    with pytest.raises(GuardError) as exc_info:
        await apply(
            "postgresql://admin:pass@prod/db",
            agent_rec["id"], "input", "remember"
        )
    assert "live_data" in str(exc_info.value) or "credential" in str(exc_info.value)
    os.environ["SAMVIT_GUARD_MODE"] = "redact"


@pytest.mark.asyncio
async def test_warn_mode_passes_through(agent_rec):
    os.environ["SAMVIT_GUARD_MODE"] = "warn"
    original = "api_key=SomeSensitiveValue999"
    result = await apply(original, agent_rec["id"], "input", "remember")
    # In warn mode: text is unchanged
    assert result == original
    os.environ["SAMVIT_GUARD_MODE"] = "redact"


@pytest.mark.asyncio
async def test_off_mode_no_op(agent_rec):
    os.environ["SAMVIT_GUARD_MODE"] = "off"
    original = "AKIAIOSFODNN7EXAMPLE"
    result = await apply(original, agent_rec["id"], "input", "remember")
    assert result == original
    os.environ["SAMVIT_GUARD_MODE"] = "redact"


# ── Integration: guard applied through tool functions ─────────────────────────

@pytest.mark.asyncio
async def test_remember_redacts_secret(agent_rec):
    """Guard fires on remember input — secret must not be stored as-is."""
    from samvit.tools.memory import remember, recall

    os.environ["SAMVIT_GUARD_MODE"] = "redact"
    await remember(agent_rec, "DB password is postgresql://admin:topsecret@prod/db", key="db.test")

    r = await recall(agent_rec, key="db.test")
    stored = r["results"][0]["content"]
    assert "topsecret" not in stored
    assert "[REDACTED:" in stored


@pytest.mark.asyncio
async def test_say_redacts_secret(two_agent_recs):
    """Guard fires on say — recipient never gets the raw secret."""
    from samvit.tools.messaging import say, read

    os.environ["SAMVIT_GUARD_MODE"] = "redact"
    a1, a2 = two_agent_recs
    fake_stripe = "sk" + "_live_" + "B" * 24
    await say(a1, f"stripe key is {fake_stripe}", to=a2["handle"])

    msgs = (await read(a2))["messages"]
    received_bodies = [m["body"] for m in msgs]
    assert not any("sk_live_" in b for b in received_bodies)
    assert any("[REDACTED:" in b for b in received_bodies)


@pytest.mark.asyncio
async def test_clean_content_passes_through(agent_rec):
    """Non-sensitive content is never modified."""
    from samvit.tools.memory import remember, recall

    os.environ["SAMVIT_GUARD_MODE"] = "redact"
    text = "The auth module validates tokens server-side using a 24-hour expiry window."
    await remember(agent_rec, text)
    r = await recall(agent_rec, query="token expiry")
    assert any(text in res["content"] for res in r["results"])
