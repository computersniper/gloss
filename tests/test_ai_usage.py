"""Usage accounting stays local, task-scoped, and honest about estimates."""
from __future__ import annotations

import asyncio

from app.providers import registry


class _ExactProvider:
    async def complete(self, system, messages, model=None):  # noqa: ARG002
        return "A short answer", {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }


class _NoUsageProvider:
    async def complete(self, system, messages, model=None):  # noqa: ARG002
        # This mirrors the current local Codex CLI contract: useful output but
        # no machine-readable token account.
        return "A short answer", {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


def test_usage_api_groups_tasks_and_marks_fallbacks(client, monkeypatch):
    monkeypatch.setitem(registry._PROVIDERS, "usage_exact", _ExactProvider())
    monkeypatch.setitem(registry._PROVIDERS, "usage_estimated", _NoUsageProvider())

    async def run_calls():
        with registry.usage_context("usage-test", paper_id="paper-usage"):
            await registry.complete(
                "system", [{"role": "user", "content": "prompt"}],
                provider="usage_exact",
            )
        with registry.usage_context("usage-test", paper_id="paper-usage"):
            await registry.complete(
                "system", [{"role": "user", "content": "prompt"}],
                provider="usage_estimated",
            )

    asyncio.run(run_calls())

    response = client.get("/api/usage?task_type=usage-test&paper_id=paper-usage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"]["tasks"] == 2
    assert payload["total"]["total_tokens"] > 18
    assert payload["total"]["average_total_tokens"] > 0
    assert payload["total"]["estimated_tasks"] == 1
    legacy_keys = {"task_type", "tasks", "prompt_tokens", "completion_tokens", "total_tokens", "average_total_tokens", "estimated_tasks"}
    assert [{k: v for k, v in row.items() if k in legacy_keys} for row in payload["by_task"]] == [
        {
            "task_type": "usage-test",
            "tasks": 2,
            "prompt_tokens": payload["by_task"][0]["prompt_tokens"],
            "completion_tokens": payload["by_task"][0]["completion_tokens"],
            "total_tokens": payload["by_task"][0]["total_tokens"],
            "average_total_tokens": payload["by_task"][0]["average_total_tokens"],
            "estimated_tasks": 1,
        }
    ]
    assert {record["estimated"] for record in payload["recent"]} == {0, 1}
