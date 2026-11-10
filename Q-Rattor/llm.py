"""
LLM backends for the resume pipeline. Claude Code (headless) first, Ollama second.

Order and limits come from environment variables so cron/launchd can tune them:
    JOOB_LLM_ORDER        default "claude,ollama"   (any subset/order; "ollama" alone = local only)
    JOOB_CLAUDE_MODEL     default "sonnet"          ("haiku" is cheaper, "sonnet" writes better)
    JOOB_MAX_CLAUDE_CALLS default 40                (hard cap per run, retries included)
    JOOB_OLLAMA_MODEL     default "qwen2.5:7b"
    JOOB_OLLAMA_URL       default http://localhost:11434/api/chat

The Claude call runs `claude -p` with no tools, no MCP servers, no settings/CLAUDE.md and no
slash commands, from a temp directory. That keeps each call at ~600 tokens of overhead instead of
~87,000 (measured), which matters when the Team plan has a weekly cap.
If Claude reports a rate/usage limit, it is switched off for the rest of the run and the next backend
in the order takes over.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

import requests

ORDER = [s.strip() for s in os.environ.get("JOOB_LLM_ORDER", "claude,ollama").split(",") if s.strip()]
CLAUDE_MODEL = os.environ.get("JOOB_CLAUDE_MODEL", "sonnet")
MAX_CLAUDE_CALLS = int(os.environ.get("JOOB_MAX_CLAUDE_CALLS", "40"))
OLLAMA_MODEL = os.environ.get("JOOB_OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_URL = os.environ.get("JOOB_OLLAMA_URL", "http://localhost:11434/api/chat")

STATE = {"claude_calls": 0, "claude_disabled": False, "claude_cost_usd": 0.0, "ollama_disabled": False}


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("no JSON object in model output")
        return json.loads(text[start:end + 1])


def _claude(system: str, user: str) -> dict:
    if STATE["claude_disabled"]:
        raise LLMError("claude disabled for this run")
    if STATE["claude_calls"] >= MAX_CLAUDE_CALLS:
        raise LLMError("claude call budget reached (%d)" % MAX_CLAUDE_CALLS)
    STATE["claude_calls"] += 1
    cmd = [
        "claude", "-p", "--model", CLAUDE_MODEL, "--output-format", "json",
        "--tools", "", "--disable-slash-commands", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--system-prompt", system,
    ]
    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True,
                              timeout=300, cwd=tempfile.gettempdir())
    except FileNotFoundError:
        STATE["claude_disabled"] = True
        raise LLMError("claude CLI not found")
    except subprocess.TimeoutExpired:
        raise LLMError("claude timed out")
    raw = (proc.stdout or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"is_error": True, "result": raw or proc.stderr}
    if proc.returncode != 0 or data.get("is_error"):
        msg = str(data.get("result") or proc.stderr)[:300]
        if re.search(r"limit|quota|usage|overloaded|429|credit", msg, re.I):
            STATE["claude_disabled"] = True
        raise LLMError("claude error: " + msg)
    STATE["claude_cost_usd"] += float(data.get("total_cost_usd") or 0)
    return _extract_json(str(data.get("result", "")))


def _ollama(system: str, user: str) -> dict:
    if STATE["ollama_disabled"]:
        raise LLMError("ollama unavailable")
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "stream": False, "format": "json", "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_predict": 1600},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, timeout=240)
        resp.raise_for_status()
    except requests.ConnectionError:
        STATE["ollama_disabled"] = True
        raise LLMError("ollama not running")
    except requests.RequestException as ex:
        raise LLMError("ollama error: %s" % ex)
    return _extract_json(resp.json()["message"]["content"])


_BACKENDS = {"claude": _claude, "ollama": _ollama}


def generate_json(system: str, user: str) -> tuple[dict, str]:
    """Try each backend in ORDER. Returns (parsed_json, backend_name)."""
    errors = []
    for name in ORDER:
        fn = _BACKENDS.get(name)
        if not fn:
            continue
        try:
            return fn(system, user), name
        except (LLMError, json.JSONDecodeError, KeyError) as ex:
            errors.append("%s: %s" % (name, ex))
    raise LLMError("; ".join(errors) or "no backends configured")


def summary() -> str:
    return "claude calls=%d cost=$%.3f%s" % (
        STATE["claude_calls"], STATE["claude_cost_usd"],
        " (disabled)" if STATE["claude_disabled"] else "")
