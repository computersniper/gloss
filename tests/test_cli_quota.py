"""Usage limits of the accounts Claude Code and Codex are signed in to."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

from app.providers import quota


def test_claude_usage_is_read_with_the_cli_login(tmp_path, monkeypatch):
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "subscriptionType": "max"}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["beta"] = request.headers["anthropic-beta"]
        return httpx.Response(200, json={
            "five_hour": {"utilization": 23.0, "resets_at": "2026-10-11T12:00:00+00:00"},
            "seven_day": {"utilization": 41.5, "resets_at": "2026-10-15T00:00:00Z"},
            "seven_day_opus": None,
        })
    real = httpx.AsyncClient
    monkeypatch.setattr(quota.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    result = asyncio.run(quota.quota("local_claude", fresh=True))
    assert seen == {"auth": "Bearer tok", "beta": "oauth-2025-04-20"}
    assert result["available"] and result["plan"] == "max"
    assert [(w["id"], w["used_percent"]) for w in result["windows"]] == [("five_hour", 23.0), ("seven_day", 41.5)]
    assert result["windows"][0]["label_zh"] == "5 小时额度" and result["windows"][0]["resets_at"] > 0


def test_claude_without_login_explains(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(quota.sys, "platform", "linux")
    result = asyncio.run(quota.quota("local_claude", fresh=True))
    assert not result["available"] and "not signed in" in result["error"]


def test_codex_limits_from_app_server(tmp_path, monkeypatch):
    server = tmp_path / "fake_codex.py"
    server.write_text(
        "import sys, json\n"
        "assert sys.argv[-1] == 'app-server'\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m.get('method') == 'initialize': print(json.dumps({'id': m['id'], 'result': {}}), flush=True)\n"
        "    elif m.get('method') == 'account/rateLimits/read': print(json.dumps({'id': m['id'], 'result': {'rateLimits': {"
        "'primary': {'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 1900000000},"
        "'secondary': {'usedPercent': 55, 'windowDurationMins': 10080, 'resetsAt': 1900500000}}}}), flush=True)\n"
        "    elif m.get('method') == 'account/read': print(json.dumps({'id': m['id'], 'result': {'account': {'planType': 'plus'}}}), flush=True)\n",
        encoding="utf-8")

    async def command():
        return [sys.executable, str(server)]
    monkeypatch.setattr("app.providers.local_codex._command", command)
    result = asyncio.run(quota.quota("local_codex", fresh=True))
    assert result["source"] == "app_server" and result["plan"] == "plus"
    assert [(w["label_zh"], w["used_percent"], w["resets_at"]) for w in result["windows"]] == [
        ("5 小时额度", 12.0, 1900000000.0), ("每周额度", 55.0, 1900500000.0)]


def test_codex_falls_back_to_its_session_log(tmp_path, monkeypatch):
    async def command():
        raise RuntimeError("no app-server")
    monkeypatch.setattr("app.providers.local_codex._command", command)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    day = tmp_path / "sessions" / "2026" / "10" / "11"
    day.mkdir(parents=True)
    event = {"timestamp": "2026-10-11T07:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": {"used_percent": 30.0, "window_minutes": 300, "resets_in_seconds": 600},
        "secondary": {"used_percent": 70.0, "window_minutes": 10080, "resets_in_seconds": 86400}}}}
    (day / "rollout-x.jsonl").write_text('{"type":"session_meta"}\n' + json.dumps(event) + "\n", encoding="utf-8")
    result = asyncio.run(quota.quota("local_codex", fresh=True))
    assert result["source"] == "session_log" and result["available"]
    assert [w["used_percent"] for w in result["windows"]] == [30.0, 70.0]
    assert result["windows"][0]["resets_at"] == quota._epoch("2026-10-11T07:00:00Z") + 600


def test_quota_endpoint_rejects_other_providers(client):
    assert client.get("/api/settings/quota?provider=openai").status_code == 400
