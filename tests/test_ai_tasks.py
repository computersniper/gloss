from __future__ import annotations

import asyncio
import json
import time

from app.providers import registry
from app.library import store
from app.plugins import manager as plugins
from app.tasks import manager as task_module


def _wait_for_terminal(client, task_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/tasks/{task_id}").json()
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("AI task did not reach a terminal state")


def test_task_api_runs_summary_and_sse_closes_after_terminal(
    client, paper_id, monkeypatch
) -> None:
    expected = {
        "tldr": "A concise result",
        "problem": "A problem",
        "method": "A method",
        "results": "A result",
        "contributions": [],
        "key_points": [],
        "limitations": [],
    }

    async def summarize(*_args, **_kwargs):
        await asyncio.sleep(0)
        return expected

    monkeypatch.setattr(task_module.summarize_feat, "summarize_paper", summarize)
    created = client.post(
        "/api/tasks",
        json={
            "feature_id": "core.summary",
            "paper_id": paper_id,
            "language": "English",
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    terminal = _wait_for_terminal(client, task_id)

    assert terminal["status"] == "completed"
    assert terminal["progress"] == 100
    assert terminal["result"] == expected
    assert terminal["cancellable"] is False
    assert terminal["agent"]["icon"]
    assert isinstance(terminal["agent"]["messages"], dict)

    with client.stream("GET", f"/api/tasks/{task_id}/events") as response:
        data_lines = [
            line.removeprefix("data: ")
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    assert len(data_lines) == 1
    assert json.loads(data_lines[0])["status"] == "completed"


def test_cached_artifact_endpoint_reads_local_summary_without_ai(
    client, paper_id, monkeypatch
) -> None:
    expected = {"tldr": "Saved locally"}
    from app.features.reading_lens import cache_key_for_paper

    store.cache_set(paper_id, cache_key_for_paper(paper_id, "summary:English"), expected)

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("artifact lookup must not contact AI")

    monkeypatch.setattr(task_module.summarize_feat, "summarize_paper", must_not_run)
    response = client.get(
        "/api/tasks/artifact",
        params={
            "feature_id": "core.summary",
            "paper_id": paper_id,
            "language": "English",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"found": True, "result": expected}


def test_plugin_result_restores_locally_and_uninstall_deletes_it(
    client, paper_id, monkeypatch
) -> None:
    plugin_id = "example.persisted-result"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Persisted Result",
        "version": "1.0.0",
        "description": "Stores a result for later reading.",
        "permissions": ["paper:read", "ai:complete"],
        "contributes": {"paper_sidebar": {"tab_name": "Persisted"}},
        "prompt": "Write a durable result.",
    }
    assert client.post(
        "/api/plugins/install", json={"manifest": manifest}
    ).status_code == 200

    async def complete(*_args, **_kwargs):
        return "Saved plugin output"

    monkeypatch.setattr(plugins, "text_complete", complete)
    created = client.post(
        "/api/tasks",
        json={
            "feature_id": f"plugin:{plugin_id}",
            "paper_id": paper_id,
            "language": "English",
        },
    )
    terminal = _wait_for_terminal(client, created.json()["id"])
    assert terminal["status"] == "completed"

    restored = client.get(
        "/api/tasks/artifact",
        params={
            "feature_id": f"plugin:{plugin_id}",
            "paper_id": paper_id,
            "language": "English",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["result"]["markdown"] == "Saved plugin output"

    removed = client.delete(f"/api/plugins/{plugin_id}")
    assert removed.status_code == 200
    assert removed.json()["cancelled_tasks"] == 0
    assert store.cache_get(
        paper_id,
        plugins._plugin_cache_key(
            plugins.validate_manifest(manifest), "English", {},
        )[0],
    ) is None


def test_network_research_plugin_reports_searching_stage(
    client, paper_id, monkeypatch
) -> None:
    plugin_id = "example.network-stage"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Network Stage",
        "version": "1.0.0",
        "author": "Test",
        "description": "Exposes network research progress.",
        "permissions": ["paper:read", "ai:complete", "network:research"],
        "prompt": "Research verified sources.",
    }
    assert client.post(
        "/api/plugins/install", json={"manifest": manifest}
    ).status_code == 200

    async def delayed_result(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        return {"status": "ready", "reason": None, "markdown": "Done"}

    monkeypatch.setattr(task_module.plugin_manager, "run_plugin", delayed_result)
    try:
        task_id = client.post(
            "/api/tasks",
            json={"feature_id": f"plugin:{plugin_id}", "paper_id": paper_id},
        ).json()["id"]
        deadline = time.monotonic() + 2
        observed = None
        while time.monotonic() < deadline:
            observed = client.get(f"/api/tasks/{task_id}").json()
            if observed["stage"] == "searching":
                break
            time.sleep(0.01)

        assert observed is not None
        assert observed["stage"] == "searching"
        assert observed["detail"]
        assert _wait_for_terminal(client, task_id)["status"] == "completed"
    finally:
        client.delete(f"/api/plugins/{plugin_id}")


def test_task_cancel_propagates_to_running_feature(
    client, paper_id, monkeypatch
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def summarize(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(task_module.summarize_feat, "summarize_paper", summarize)
    task_id = client.post(
        "/api/tasks",
        json={"feature_id": "core.summary", "paper_id": paper_id},
    ).json()["id"]

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/tasks/{task_id}").json()
        if snapshot["stage"] == "thinking":
            break
        time.sleep(0.01)

    response = client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["status"] in {"cancelling", "cancelled"}
    terminal = _wait_for_terminal(client, task_id)
    assert terminal["status"] == "cancelled"
    assert cancelled.is_set()


def test_uninstall_cancels_running_plugin_before_deleting_records(
    client, paper_id, monkeypatch
) -> None:
    plugin_id = "example.running-uninstall"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Running Uninstall",
        "version": "1.0.0",
        "description": "Exercises uninstall cancellation.",
        "permissions": ["paper:read", "ai:complete"],
        "contributes": {"paper_sidebar": {"tab_name": "Running"}},
        "prompt": "Wait for cancellation.",
    }
    assert client.post(
        "/api/plugins/install", json={"manifest": manifest}
    ).status_code == 200
    cancelled = asyncio.Event()

    async def run_plugin(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(task_module.plugin_manager, "run_plugin", run_plugin)
    task_id = client.post(
        "/api/tasks",
        json={
            "feature_id": f"plugin:{plugin_id}",
            "paper_id": paper_id,
        },
    ).json()["id"]

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/tasks/{task_id}").json()
        if snapshot["stage"] == "thinking":
            break
        time.sleep(0.01)

    removed = client.delete(f"/api/plugins/{plugin_id}")
    assert removed.status_code == 200
    assert removed.json()["cancelled_tasks"] == 1
    assert _wait_for_terminal(client, task_id)["status"] == "cancelled"
    assert cancelled.is_set()


def test_task_manager_has_no_global_concurrency_limit(monkeypatch) -> None:
    async def exercise() -> None:
        manager = task_module.AITaskManager()
        started: set[str] = set()
        both_started = asyncio.Event()
        release = asyncio.Event()

        monkeypatch.setattr(task_module.store, "get_paper", lambda _paper_id: {"id": "p"})

        async def summarize(paper_id, **_kwargs):
            started.add(paper_id)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            return {"paper": paper_id}

        monkeypatch.setattr(task_module.summarize_feat, "summarize_paper", summarize)
        one = await manager.submit(feature_id="core.summary", paper_id="paper-1")
        two = await manager.submit(feature_id="core.summary", paper_id="paper-2")
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert started == {"paper-1", "paper-2"}
        release.set()
        await asyncio.gather(
            manager._tasks[one["id"]].runner,
            manager._tasks[two["id"]].runner,
        )
        await manager.shutdown()

    asyncio.run(exercise())


def test_task_manager_can_cancel_before_runner_starts(monkeypatch) -> None:
    async def exercise() -> None:
        manager = task_module.AITaskManager()
        monkeypatch.setattr(task_module.store, "get_paper", lambda _paper_id: {"id": "p"})
        created = await manager.submit(feature_id="core.summary", paper_id="paper-1")
        cancelled = await manager.cancel(created["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["finished_at"] is not None
        await manager.shutdown()

    asyncio.run(exercise())


def test_shared_codex_session_sends_paper_once_and_serializes(monkeypatch) -> None:
    async def exercise() -> None:
        calls: list[dict] = []
        sessions: dict[tuple[str, str, str], str] = {}
        active = 0
        max_active = 0

        class SessionProvider:
            async def complete(self, *_args, **_kwargs):
                raise AssertionError("shared mode must use complete_session")

            async def complete_session(
                self, system, messages, model=None, *, session_id=None
            ):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                calls.append(
                    {
                        "system": system,
                        "content": messages[0]["content"],
                        "session_id": session_id,
                    }
                )
                await asyncio.sleep(0.01)
                active -= 1
                return "ok", {}, session_id or "session-1"

        monkeypatch.setitem(registry._PROVIDERS, "local_codex", SessionProvider())
        monkeypatch.setattr(
            registry.store,
            "load_parsed",
            lambda _paper_id: {"full_text": "canonical full paper"},
        )
        monkeypatch.setattr(
            registry.store,
            "get_provider_session",
            lambda paper_id, provider, fingerprint: (
                {"session_id": sessions[(paper_id, provider, fingerprint)]}
                if (paper_id, provider, fingerprint) in sessions
                else None
            ),
        )
        monkeypatch.setattr(
            registry.store,
            "set_provider_session",
            lambda paper_id, provider, fingerprint, session_id: sessions.__setitem__(
                (paper_id, provider, fingerprint), session_id
            ),
        )
        monkeypatch.setattr(
            registry.store,
            "delete_provider_session",
            lambda paper_id, provider, fingerprint: sessions.pop(
                (paper_id, provider, fingerprint), None
            ),
        )

        async def invoke(instruction: str):
            with registry.feature_context(
                paper_id="paper-1",
                context_mode="shared_session",
                effort="low",
            ):
                return await registry.complete(
                    "system",
                    [
                        {
                            "role": "user",
                            "content": (
                                f"{instruction}\n\n=== PAPER ===\n"
                                "canonical full paper"
                            ),
                        }
                    ],
                    provider="local_codex",
                )

        await asyncio.gather(invoke("summarize"), invoke("write notes"))
        assert max_active == 1
        assert "=== PAPER ===" in calls[0]["content"]
        assert calls[0]["session_id"] is None
        assert "=== PAPER ===" not in calls[1]["content"]
        assert "already present" in calls[1]["content"]
        assert calls[1]["session_id"] == "session-1"

    asyncio.run(exercise())
