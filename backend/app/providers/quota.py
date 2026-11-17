"""Plan usage limits (the 5-hour and weekly windows) of the accounts the local
Claude Code and Codex CLIs are signed in to.

Read-only and best effort: nothing here refreshes or changes a login.

- Claude Code: the OAuth usage endpoint the CLI's own ``/usage`` reads, with
  the token from the CLI's credentials file (or the macOS keychain).
- Codex: ``codex app-server``'s ``account/rateLimits/read``; when that is not
  available, the rate limits Codex recorded in its newest session log.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_SECONDS = 60
_cache: dict[str, tuple[float, dict]] = {}

_CLAUDE_LABELS = {
    "five_hour": ("5-hour limit", "5 小时额度"),
    "seven_day": ("Weekly · all models", "每周 · 全部模型"),
    "seven_day_opus": ("Weekly · Opus", "每周 · Opus"),
    "seven_day_sonnet": ("Weekly · Sonnet", "每周 · Sonnet"),
    "seven_day_oauth_apps": ("Weekly · apps", "每周 · 应用"),
}


def _result(provider: str, **fields: Any) -> dict:
    return {"provider": provider, "available": False, "plan": None, "windows": [], "source": None,
            "error": None, "checked_at": time.time(), **fields}


def _epoch(value: Any) -> float | None:
    """Unix seconds from seconds, milliseconds or an ISO-8601 string."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e12 else float(value)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window_label(minutes: float | None) -> tuple[str, str]:
    if not minutes:
        return ("Usage limit", "用量额度")
    if abs(minutes - 300) < 1:
        return ("5-hour limit", "5 小时额度")
    if abs(minutes - 10080) < 1:
        return ("Weekly limit", "每周额度")
    if minutes % 1440 == 0:
        return (f"{int(minutes // 1440)}-day limit", f"{int(minutes // 1440)} 天额度")
    if minutes % 60 == 0:
        return (f"{int(minutes // 60)}-hour limit", f"{int(minutes // 60)} 小时额度")
    return (f"{int(minutes)}-minute limit", f"{int(minutes)} 分钟额度")


# -- Claude Code ------------------------------------------------------------------
def _claude_credentials() -> dict | None:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        return json.loads((base / ".credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if sys.platform == "darwin":  # the CLI keeps its login in the keychain there
        try:
            out = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                return json.loads(out.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def parse_claude_usage(data: dict) -> list[dict]:
    windows = []
    for key, value in (data or {}).items():
        if not isinstance(value, dict) or value.get("utilization") is None:
            continue
        en, zh = _CLAUDE_LABELS.get(key, (key.replace("_", " ").capitalize(), key.replace("_", " ")))
        windows.append({"id": key, "label": en, "label_zh": zh,
                        "used_percent": round(float(value["utilization"]), 1),
                        "resets_at": _epoch(value.get("resets_at")),
                        "window_minutes": 300 if key == "five_hour" else 10080 if key.startswith("seven_day") else None})
    order = list(_CLAUDE_LABELS)
    return sorted(windows, key=lambda w: order.index(w["id"]) if w["id"] in order else len(order))


async def claude_quota() -> dict:
    creds = _claude_credentials()
    oauth = (creds or {}).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    plan = oauth.get("subscriptionType") or oauth.get("rateLimitTier")
    if not token:
        return _result("local_claude", error="Claude Code is not signed in with a Claude subscription (run `claude` and log in).")
    expires = _epoch(oauth.get("expiresAt"))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(CLAUDE_USAGE_URL, headers={
                "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json", "User-Agent": "claude-code/glossx"})
    except httpx.HTTPError as error:
        return _result("local_claude", plan=plan, error=f"Could not reach Anthropic: {error}")
    if response.status_code == 401:
        hint = " The saved login has expired; run Claude Code once to refresh it." if expires and expires < time.time() else ""
        return _result("local_claude", plan=plan, error="Anthropic rejected the saved login." + hint)
    if response.status_code != 200:
        return _result("local_claude", plan=plan, error=f"Anthropic returned HTTP {response.status_code}.")
    try:
        windows = parse_claude_usage(response.json())
    except ValueError:
        return _result("local_claude", plan=plan, error="Anthropic returned an unreadable answer.")
    return _result("local_claude", available=bool(windows), plan=plan, windows=windows, source="oauth_usage",
                   error=None if windows else "This account reports no usage limits.")


# -- Codex ------------------------------------------------------------------------
def _get(d: dict, *names: str) -> Any:
    for name in names:
        if d.get(name) is not None:
            return d[name]
    return None


def parse_codex_limits(limits: dict, observed_at: float | None = None) -> list[dict]:
    """Both ``account/rateLimits/read`` (camelCase) and session-log ``rate_limits``
    (snake_case) shapes: ``primary`` / ``secondary`` windows."""
    windows = []
    for key in ("primary", "secondary"):
        w = _get(limits or {}, key, f"{key}_window", f"{key}Window")
        if not isinstance(w, dict):
            continue
        used = _get(w, "usedPercent", "used_percent")
        if used is None:
            continue
        minutes = _get(w, "windowDurationMins", "window_minutes", "windowMinutes")
        if minutes is None and _get(w, "limit_window_seconds") is not None:
            minutes = float(w["limit_window_seconds"]) / 60
        resets = _epoch(_get(w, "resetsAt", "resets_at", "reset_at"))
        after = _get(w, "resets_in_seconds", "reset_after_seconds", "resetsInSeconds")
        if resets is None and after is not None:
            resets = (observed_at or time.time()) + float(after)
        en, zh = _window_label(float(minutes) if minutes else None)
        windows.append({"id": key, "label": en, "label_zh": zh, "used_percent": round(float(used), 1),
                        "resets_at": resets, "window_minutes": float(minutes) if minutes else None})
    return windows


async def _codex_app_server(command: list[str], timeout: float = 15) -> tuple[dict, str | None]:
    """Ask ``codex app-server`` for the account's rate limits (and plan)."""
    from ..platform_support import subprocess_group_options, terminate_process_tree

    proc = await asyncio.create_subprocess_exec(
        *command, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **subprocess_group_options())

    async def send(message: dict) -> None:
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    async def reply(request_id: int) -> dict:
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise RuntimeError("codex app-server closed the connection")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == request_id:
                if message.get("error"):
                    raise RuntimeError(str(message["error"].get("message") or message["error"]))
                return message.get("result") or {}

    try:
        async with asyncio.timeout(timeout):
            await send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "glossx", "title": "GlossX", "version": "1"}}})
            await reply(0)
            await send({"method": "initialized"})
            await send({"id": 1, "method": "account/rateLimits/read"})
            limits = await reply(1)
            plan = None
            try:
                await send({"id": 2, "method": "account/read", "params": {}})
                account = (await reply(2)).get("account") or {}
                plan = account.get("planType") or account.get("plan_type")
            except RuntimeError:
                pass
            return limits.get("rateLimits") or limits.get("rate_limits") or limits, plan
    finally:
        if proc.returncode is None:
            await terminate_process_tree(proc)


def _codex_session_log() -> tuple[dict, float] | None:
    """The newest ``rate_limits`` Codex wrote to its session logs."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    files = sorted((home / "sessions").rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or event.get("msg") or {}
            limits = payload.get("rate_limits") or (payload.get("info") or {}).get("rate_limits")
            if isinstance(limits, dict):
                return limits, _epoch(event.get("timestamp")) or path.stat().st_mtime
    return None


async def codex_quota() -> dict:
    from .local_codex import _command

    plan = None
    error = None
    try:
        command = await _command()
        limits, plan = await _codex_app_server(command)
        windows = parse_codex_limits(limits)
        if windows:
            return _result("local_codex", available=True, plan=plan, windows=windows, source="app_server")
        error = "Codex reports no usage limits for this account."
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        error = str(exc) or type(exc).__name__
    logged = await asyncio.to_thread(_codex_session_log)
    if logged:
        limits, observed = logged
        windows = parse_codex_limits(limits, observed)
        if windows:
            return _result("local_codex", available=True, plan=plan, windows=windows, source="session_log",
                           observed_at=observed)
    return _result("local_codex", plan=plan, error=error or "Codex has not reported usage limits yet; run it once.")


async def quota(provider: str, *, fresh: bool = False) -> dict:
    cached = _cache.get(provider)
    if cached and not fresh and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    if provider == "local_claude":
        result = await claude_quota()
    elif provider == "local_codex":
        result = await codex_quota()
    else:
        return _result(provider, error="Usage limits are only available for Claude Code and Codex.")
    _cache[provider] = (time.time(), result)
    return result
