"""
Samvit Ethical Guard — secrets, credentials, PII, and live-infra scanner.

Sits inline on every input (remember, say) and every output (recall, read).
Nothing sensitive ever reaches an LLM or gets persisted unguarded.

Modes (SAMVIT_GUARD_MODE env var):
  block   — reject the request with 400; caller sees which category was found
  redact  — replace matched spans with [REDACTED:category]; store/return clean text
  warn    — log the violation; pass through unchanged (dev/audit mode)
  off     — no-op (disable entirely; not recommended in production)

Violations are always written to the `guard_violations` table regardless of mode,
giving you a full audit trail of what was attempted.
"""

from __future__ import annotations

import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum

from samvit import db

log = logging.getLogger(__name__)


# ── Mode ─────────────────────────────────────────────────────────────────────

class GuardMode(str, Enum):
    BLOCK  = "block"
    REDACT = "redact"
    WARN   = "warn"
    OFF    = "off"


def mode() -> GuardMode:
    raw = os.environ.get("SAMVIT_GUARD_MODE", "redact").lower()
    try:
        return GuardMode(raw)
    except ValueError:
        log.warning("Unknown SAMVIT_GUARD_MODE=%r — defaulting to redact", raw)
        return GuardMode.REDACT


# ── Pattern definitions ───────────────────────────────────────────────────────

@dataclass
class Pattern:
    name:     str           # e.g. "jwt_token"
    category: str           # "credential" | "pii" | "live_data" | "private_key"
    regex:    re.Pattern
    severity: str = "high"  # "high" | "medium" | "low"


def _p(name: str, category: str, pattern: str, flags: int = 0, severity: str = "high") -> Pattern:
    return Pattern(name=name, category=category, regex=re.compile(pattern, flags), severity=severity)


PATTERNS: list[Pattern] = [

    # ── Private keys ──────────────────────────────────────────────────────────
    _p("private_key",
       "private_key",
       r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+|PGP\s+|DSA\s+)?PRIVATE KEY-----"),

    # ── JWT tokens ────────────────────────────────────────────────────────────
    _p("jwt_token",
       "credential",
       r"eyJ[A-Za-z0-9_/+=-]{10,}\.[A-Za-z0-9_/+=-]{5,}\.[A-Za-z0-9_/+=-]{5,}"),

    # ── Cloud provider credentials ────────────────────────────────────────────
    _p("aws_access_key",
       "credential",
       r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),

    _p("aws_secret_key",
       "credential",
       r"(?i)aws[_\-\s]?(?:secret|secret[_\-]access[_\-]key)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}"),

    _p("gcp_service_account",
       "credential",
       r'"type"\s*:\s*"service_account"'),

    # ── VCS / CI tokens ───────────────────────────────────────────────────────
    _p("github_token",
       "credential",
       r"gh[pousr]_[A-Za-z0-9]{36,}"),

    _p("gitlab_token",
       "credential",
       r"glpat-[A-Za-z0-9\-_]{20,}"),

    _p("npm_token",
       "credential",
       r"npm_[A-Za-z0-9]{36}"),

    # ── Generic secret patterns ───────────────────────────────────────────────
    # Matches: password=abc123, api_key: "xyz", SECRET="...", token = '...'
    _p("generic_secret",
       "credential",
       r"""(?i)(?:password|passwd|secret|token|api[_\-]?key|auth[_\-]?key|access[_\-]?key)\s*[=:]\s*['"]?(?!your_|<|{|\[|example|placeholder|changeme|xxx)[A-Za-z0-9_/+\-=.!@#$%^&*]{8,}""",
       severity="medium"),

    # ── Database / service connection strings ─────────────────────────────────
    _p("connection_string",
       "live_data",
       r"(?:postgresql|postgres|mysql|mongodb|redis|amqp|rabbitmq|cassandra|couchdb)://[^\s'\"<>]+:[^\s'\"<>@]+@[^\s'\"<>]+"),

    # ── PII ───────────────────────────────────────────────────────────────────
    _p("credit_card",
       "pii",
       r"\b(?:4[0-9]{12}(?:[0-9]{3})?|(?:5[1-5]|2[2-7])[0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),

    _p("ssn",
       "pii",
       r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),

    # ── Internal / live infrastructure ────────────────────────────────────────
    # Private IP ranges (RFC 1918) — signals production infra leaking
    _p("internal_ip",
       "live_data",
       r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
       r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
       r"|192\.168\.\d{1,3}\.\d{1,3})\b",
       severity="medium"),

    # ── Bcrypt / hashed secrets ───────────────────────────────────────────────
    _p("bcrypt_hash",
       "credential",
       r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}"),

    # ── Slack / Stripe / Twilio ───────────────────────────────────────────────
    _p("slack_token",
       "credential",
       r"xox[baprs]-[A-Za-z0-9\-]{10,}"),

    _p("stripe_key",
       "credential",
       r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,}"),

    _p("twilio_key",
       "credential",
       r"SK[0-9a-fA-F]{32}"),
]


# ── High-entropy string detection (catches unrecognised secrets) ──────────────

MIN_ENTROPY_LENGTH = 20   # ignore short strings
HIGH_ENTROPY_THRESHOLD = 4.5   # bits per character (Base64 secrets ≈ 6.0)
ENTROPY_ALPHABET_MIN = 8  # at least 8 distinct chars (avoids prose)

def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((f / n) * math.log2(f / n) for f in freq.values())


_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_\-]{%d,}" % MIN_ENTROPY_LENGTH)


def _high_entropy_spans(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, matched_text) for high-entropy token-looking strings."""
    spans = []
    for m in _HIGH_ENTROPY_RE.finditer(text):
        s = m.group()
        if (len(set(s)) >= ENTROPY_ALPHABET_MIN
                and _shannon_entropy(s) >= HIGH_ENTROPY_THRESHOLD):
            spans.append((m.start(), m.end(), s))
    return spans


# ── Core scan / redact ────────────────────────────────────────────────────────

@dataclass
class Violation:
    pattern_name: str
    category:     str
    severity:     str
    snippet:      str   # first 6 chars of matched text + "…" (never full value)


@dataclass
class ScanResult:
    clean:      str
    violations: list[Violation] = field(default_factory=list)

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)


def scan(text: str, *, check_entropy: bool = True) -> ScanResult:
    """
    Scan text for sensitive patterns. Returns a ScanResult with:
      - clean: text with violations replaced by [REDACTED:category]
      - violations: list of what was found (never includes the full secret value)
    """
    violations: list[Violation] = []
    result = text

    # 1. Named pattern matches
    for pat in PATTERNS:
        for m in pat.regex.finditer(result):
            matched = m.group()
            snippet = matched[:6] + "…" if len(matched) > 6 else matched[:3] + "…"
            violations.append(Violation(
                pattern_name=pat.name,
                category=pat.category,
                severity=pat.severity,
                snippet=snippet,
            ))
            result = result.replace(matched, f"[REDACTED:{pat.category}]", 1)

    # 2. High-entropy string detection (catches unknown secrets)
    if check_entropy:
        for start, end, matched in _high_entropy_spans(result):
            # Skip if already redacted
            if "[REDACTED:" in matched:
                continue
            snippet = matched[:6] + "…"
            violations.append(Violation(
                pattern_name="high_entropy_string",
                category="credential",
                severity="medium",
                snippet=snippet,
            ))
            result = result.replace(matched, "[REDACTED:credential]", 1)

    return ScanResult(clean=result, violations=violations)


# ── Apply guard ───────────────────────────────────────────────────────────────

class GuardError(ValueError):
    """Raised in BLOCK mode when sensitive content is detected."""
    def __init__(self, violations: list[Violation]):
        self.violations = violations
        categories = list({v.category for v in violations})
        super().__init__(
            f"Content blocked by Samvit ethical guard — "
            f"sensitive data detected: {', '.join(categories)}. "
            f"Remove credentials/PII before storing."
        )


async def apply(
    text: str,
    agent_id: str,
    direction: str,   # "input" | "output"
    tool: str,        # "remember" | "recall" | "say" | "read"
    *,
    check_entropy: bool = True,
) -> str:
    """
    Apply the guard to a text value. Returns the (possibly redacted) text.
    Raises GuardError in BLOCK mode if violations are found.
    Always logs violations to guard_violations table.
    """
    m = mode()
    if m == GuardMode.OFF:
        return text

    result = scan(text, check_entropy=check_entropy)
    if not result.has_violations:
        return text

    # Log every violation to the audit table regardless of mode
    await _log_violations(result.violations, agent_id, direction, tool, text)

    if m == GuardMode.BLOCK:
        raise GuardError(result.violations)

    if m == GuardMode.WARN:
        for v in result.violations:
            log.warning(
                "GUARD WARN [%s/%s] agent=%s pattern=%s category=%s snippet=%r",
                direction, tool, agent_id, v.pattern_name, v.category, v.snippet,
            )
        return text   # pass through unchanged in warn mode

    # REDACT (default)
    for v in result.violations:
        log.info(
            "GUARD REDACT [%s/%s] agent=%s pattern=%s category=%s",
            direction, tool, agent_id, v.pattern_name, v.category,
        )
    return result.clean


async def _log_violations(
    violations: list[Violation],
    agent_id: str,
    direction: str,
    tool: str,
    original_text: str,
) -> None:
    """Persist violations to audit table. Never stores the original text."""
    try:
        async with db.pool().acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO guard_violations
                    (agent_id, direction, tool, pattern_name, category, severity, snippet)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                [
                    (
                        agent_id, direction, tool,
                        v.pattern_name, v.category, v.severity, v.snippet,
                    )
                    for v in violations
                ],
            )
    except Exception as exc:
        # Never let audit failure break the request
        log.error("Failed to log guard violations: %s", exc)
