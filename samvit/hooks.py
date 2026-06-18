"""
Samvit hook CLI — called by Claude Code's settings.json hooks.

Hooks are fire-and-forget: they MUST exit 0 and must be fast.
Network errors are swallowed — never let a hook break agent operation.

Hook types:
  pre-tool   — before a tool call: inject relevant memories as context notes
  post-tool  — after a tool call: optionally auto-remember significant outputs
  stop       — session ending: summarise and remember key decisions

settings.json wiring:
  {
    "hooks": {
      "PreToolUse":  [{"matcher":".*","hooks":[{"type":"command","command":"python -m samvit.hooks pre-tool"}]}],
      "PostToolUse": [{"matcher":".*","hooks":[{"type":"command","command":"python -m samvit.hooks post-tool"}]}],
      "Stop":        [{"hooks":[{"type":"command","command":"python -m samvit.hooks stop"}]}]
    }
  }

Environment variables:
  SAMVIT_URL           — default http://localhost:8765
  SAMVIT_HOOK_HANDLE   — agent handle (defaults to $USER)
  SAMVIT_HOOK_TOKEN    — bearer token (loaded from ~/.samvit/credentials.json if absent)
  SAMVIT_HOOK_TIMEOUT  — max seconds per hook (default 2)
  SAMVIT_AUTO_REMEMBER — if "1", PostToolUse auto-remembers large Write/Edit outputs
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SAMVIT_URL    = os.environ.get("SAMVIT_URL", "http://localhost:8765")
HOOK_HANDLE   = os.environ.get("SAMVIT_HOOK_HANDLE", os.environ.get("USER", "agent"))
HOOK_TIMEOUT  = float(os.environ.get("SAMVIT_HOOK_TIMEOUT", "2"))
AUTO_REMEMBER = os.environ.get("SAMVIT_AUTO_REMEMBER", "1") == "1"

# Tools whose outputs are worth auto-remembering
AUTO_REMEMBER_TOOLS = {"Write", "Edit", "Bash"}
# Minimum output length to bother remembering
AUTO_REMEMBER_MIN_LEN = 200


def _load_token() -> str | None:
    env_token = os.environ.get("SAMVIT_HOOK_TOKEN")
    if env_token:
        return env_token
    creds_path = Path.home() / ".samvit" / "credentials.json"
    if not creds_path.exists():
        return None
    try:
        data = json.loads(creds_path.read_text())
        entry = data.get(HOOK_HANDLE) or data
        return entry.get("token")
    except Exception:
        return None


def _headers() -> dict | None:
    token = _load_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _call(path: str, body: dict) -> dict | None:
    headers = _headers()
    if not headers:
        return None
    try:
        with httpx.Client(timeout=HOOK_TIMEOUT) as client:
            r = client.post(f"{SAMVIT_URL}{path}", json=body, headers=headers)
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        log.debug("Samvit hook call failed (non-fatal): %s", exc)
    return None


def hook_pre_tool(stdin_data: dict) -> None:
    """
    Inject relevant memories as a context note before a tool call.
    Outputs a JSON block that Claude Code appends to tool context.
    """
    tool_name = stdin_data.get("tool_name", "")
    tool_input = stdin_data.get("tool_input", {})

    # Build a query from tool context
    query_parts = [tool_name]
    for v in tool_input.values():
        if isinstance(v, str) and len(v) < 200:
            query_parts.append(v)
    query = " ".join(query_parts).strip()

    if not query:
        return

    try:
        result = _call("/v1/tools/call", {
            "tool": "recall",
            "params": {"query": query, "limit": 3, "namespace": "global"},
        })
    except Exception as exc:
        log.debug("Samvit pre-tool hook failed (non-fatal): %s", exc)
        return
    if not result or not result.get("results"):
        return

    memories = result["results"]
    lines = ["[Samvit context]"]
    for m in memories:
        lines.append(f"  • {m['content'][:200]} (score {m['score']:.2f})")

    # Output to stdout — Claude Code appends this to the tool context
    print("\n".join(lines))


def hook_post_tool(stdin_data: dict) -> None:
    """
    After significant tool calls, optionally auto-remember the output.
    Only fires if SAMVIT_AUTO_REMEMBER=1 (default on).
    """
    if not AUTO_REMEMBER:
        return

    tool_name = stdin_data.get("tool_name", "")
    if tool_name not in AUTO_REMEMBER_TOOLS:
        return

    tool_input  = stdin_data.get("tool_input", {})
    tool_output = stdin_data.get("tool_output", {})

    # Decide what's worth remembering
    content = None
    if tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        file_content = tool_input.get("content", "")
        if len(file_content) > AUTO_REMEMBER_MIN_LEN:
            content = f"Wrote {file_path}: {file_content[:400]}…"
    elif tool_name in ("Edit",):
        file_path = tool_input.get("file_path", "")
        new_str   = tool_input.get("new_string", "")
        if len(new_str) > AUTO_REMEMBER_MIN_LEN:
            content = f"Edited {file_path}: {new_str[:400]}…"
    elif tool_name == "Bash":
        output = tool_output.get("output", "")
        cmd    = tool_input.get("command", "")
        if len(output) > AUTO_REMEMBER_MIN_LEN:
            content = f"Ran `{cmd[:100]}` → {output[:400]}"

    if content:
        try:
            _call("/v1/tools/call", {
                "tool": "remember",
                "params": {"content": content, "namespace": "global"},
            })
        except Exception as exc:
            log.debug("Samvit post-tool hook failed (non-fatal): %s", exc)


def hook_stop(stdin_data: dict) -> None:
    """
    Session ending: remember the stop reason / summary as a session note.
    """
    reason = stdin_data.get("stop_reason", "")
    usage  = stdin_data.get("usage", {})
    if reason:
        summary = f"Session ended: {reason}. Tokens used: {usage}"
        try:
            _call("/v1/tools/call", {
                "tool": "remember",
                "params": {
                    "content": summary,
                    "key": f"session.{HOOK_HANDLE}.last",
                    "namespace": HOOK_HANDLE,
                },
            })
        except Exception as exc:
            log.debug("Samvit stop hook failed (non-fatal): %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) < 2:
        print("Usage: python -m samvit.hooks <pre-tool|post-tool|stop>", file=sys.stderr)
        sys.exit(0)  # never exit non-zero

    hook_type = sys.argv[1]
    try:
        stdin_data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except Exception:
        stdin_data = {}

    try:
        if hook_type == "pre-tool":
            hook_pre_tool(stdin_data)
        elif hook_type == "post-tool":
            hook_post_tool(stdin_data)
        elif hook_type == "stop":
            hook_stop(stdin_data)
    except Exception as exc:
        # Hooks must NEVER crash — log and exit 0
        log.debug("Hook error (non-fatal): %s", exc)

    sys.exit(0)  # always exit 0


if __name__ == "__main__":
    main()
