"""Manifest plugin marketplace and installation lifecycle."""

import asyncio

import pytest


def test_marketplace_install_and_uninstall(client):
    snapshot = client.get("/api/plugins")
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["api_version"] == 1
    assert {item["status"] for item in body["contribution_points"]} >= {"stable", "planned"}
    assert len(body["coming_soon"]) >= 4
    assert {item["id"] for item in body["core"]} >= {
        "core.summary", "core.notes", "core.personal-notes", "core.mindmap"
    }
    assert {item["id"] for item in body["marketplace"]} >= {
        "gloss.deep-research",
        "gloss.critical-review",
        "gloss.implementation-blueprint",
        "gloss.evidence-table",
        "gloss.presentation-outline",
    }
    plugin = body["marketplace"][0]

    try:
        installed = client.post(f"/api/plugins/{plugin['id']}/install")
        assert installed.status_code == 200, installed.text
        assert installed.json()["id"] == plugin["id"]
        after = client.get("/api/plugins").json()
        assert plugin["id"] in {item["id"] for item in after["installed"]}
        market_item = next(item for item in after["marketplace"] if item["id"] == plugin["id"])
        assert market_item["installed"] is True
    finally:
        client.delete(f"/api/plugins/{plugin['id']}")

    assert plugin["id"] not in {
        item["id"] for item in client.get("/api/plugins").json()["installed"]
    }


def test_plugin_uninstall_removes_generated_cache(client):
    from app.library import store

    plugin_id = "example.cache-cleanup"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Cache Cleanup",
        "version": "1.0.0",
        "author": "Test",
        "description": "Checks uninstall cleanup.",
        "permissions": ["paper:read", "ai:complete"],
        "prompt": "Summarize the paper.",
    }
    assert client.post("/api/plugins/install", json={"manifest": manifest}).status_code == 200
    store.cache_set("paper-a", f"plugin:{plugin_id}:1.0.0:prompt:zh", "cached")
    store.cache_set("paper-a", "summary:zh", "keep")

    assert client.delete(f"/api/plugins/{plugin_id}").status_code == 200
    assert store.cache_get("paper-a", f"plugin:{plugin_id}:1.0.0:prompt:zh") is None
    assert store.cache_get("paper-a", "summary:zh") == "keep"


def test_custom_manifest_install_is_declarative_and_validated(client):
    manifest = {
        "id": "example.my-reading-lens",
        "api_version": 1,
        "name": "My Reading Lens",
        "version": "1.2.0",
        "author": "Reader",
        "description": "Analyze the paper using my own checklist.",
        "tab_name": "My Lens",
        "permissions": ["paper:read", "ai:complete"],
        "prompt": "List three claims and the evidence supplied for each claim.",
    }
    installed = client.post("/api/plugins/install", json={"manifest": manifest})
    assert installed.status_code == 200, installed.text
    assert installed.json()["output"] == "markdown"
    assert installed.json()["contributes"]["paper_sidebar"]["tab_name"] == "My Lens"
    assert "entry" not in installed.json()
    try:
        assert manifest["id"] in {
            item["id"] for item in client.get("/api/plugins").json()["installed"]
        }
    finally:
        client.delete(f"/api/plugins/{manifest['id']}")

    invalid = {**manifest, "id": "../outside"}
    rejected = client.post("/api/plugins/install", json={"manifest": invalid})
    assert rejected.status_code == 400

    future = {**manifest, "id": "example.future", "api_version": 99}
    rejected = client.post("/api/plugins/install", json={"manifest": future})
    assert rejected.status_code == 400


def test_plugin_manifest_schema_and_template(client):
    schema = client.get("/api/plugins/schema")
    assert schema.status_code == 200
    assert schema.json()["properties"]["api_version"]["const"] == 1
    assert "paper_sidebar" in schema.json()["properties"]["contributes"]["properties"]
    assert "configuration" in schema.json()["properties"]["contributes"]["properties"]
    assert "agent" in schema.json()["properties"]
    assert "formulas" in schema.json()["properties"]["requirements"]["items"]["enum"]

    template = client.get("/api/plugins/template")
    assert template.status_code == 200
    assert template.json()["api_version"] == 1
    assert template.json()["contributes"]["paper_sidebar"]["tab_name"]
    assert template.json()["contributes"]["configuration"]["properties"]
    assert template.json()["agent"]["messages"]["reading"]
    assert template.json()["requirements"] == ["body_text"]


def test_plugin_run_requires_installation(client, paper_id):
    response = client.post(
        f"/api/plugins/not-installed/papers/{paper_id}/run", json={}
    )
    assert response.status_code == 400


def test_marketplace_plugins_declare_their_input_requirements(client):
    marketplace = {item["id"]: item for item in client.get("/api/plugins").json()["marketplace"]}
    assert all(item["requirements"] for item in marketplace.values())
    assert marketplace["gloss.deep-research"]["requirements"] == ["body_text"]
    assert marketplace["gloss.equation-guide"]["requirements"] == ["body_text", "formulas"]
    assert marketplace["gloss.reproducibility-checklist"]["requirements"] == [
        "body_text", "method_content"
    ]
    assert marketplace["gloss.implementation-blueprint"]["requirements"] == [
        "body_text", "method_content"
    ]
    assert marketplace["gloss.evidence-table"]["requirements"] == [
        "body_text", "claims_or_evidence"
    ]
    assert marketplace["gloss.terminology-glossary"]["requirements"] == ["body_text", "terms"]
    assert marketplace["gloss.presentation-outline"]["requirements"] == ["body_text"]
    assert marketplace["gloss.reading-plan"]["requirements"] == ["body_text"]


def test_deep_research_plugin_is_configurable_and_installable(client):
    snapshot = client.get("/api/plugins").json()
    deep_research = next(
        item for item in snapshot["marketplace"]
        if item["id"] == "gloss.deep-research"
    )

    assert deep_research["name_zh"] == "深度研究"
    assert deep_research["tab_name"] == "Research"
    assert deep_research["version"] == "1.1.0"
    assert "network:research" in deep_research["permissions"]
    assert "VERIFIED NETWORK RESEARCH" in deep_research["prompt"]
    assert "external browsing" not in deep_research["prompt"]
    assert "Never invent citations" in deep_research["prompt"]
    properties = deep_research["contributes"]["configuration"]["properties"]
    assert properties["focus"]["default"] == "comprehensive"
    assert properties["depth"]["default"] == "exhaustive"
    assert properties["includeResearchAgenda"]["default"] is True
    assert properties["citePaperLocations"]["default"] is True

    try:
        installed = client.post("/api/plugins/gloss.deep-research/install")
        assert installed.status_code == 200, installed.text
        assert installed.json()["agent"]["name"] == "Deep Researcher"
        assert installed.json()["id"] in {
            item["id"] for item in client.get("/api/plugins").json()["installed"]
        }
    finally:
        client.delete("/api/plugins/gloss.deep-research")


def test_deep_research_retrieval_reaches_prompt_and_cached_metadata(
    client, paper_id, monkeypatch
):
    from app.plugins import manager

    assert client.post("/api/plugins/gloss.deep-research/install").status_code == 200
    evidence = {
        "searched_at": "2026-08-14T08:00:00+00:00",
        "queries": ["Attention Is All You Need"],
        "providers": ["Semantic Scholar", "OpenAlex"],
        "source_count": 1,
        "target": {"title": "Attention Is All You Need"},
        "sources": [{
            "source_id": "S1",
            "title": "Retrieved Work",
            "authors": ["A. Author"],
            "year": 2024,
            "abstract": "Evidence",
            "url": "https://example.org/retrieved",
            "doi": "10.1000/retrieved",
            "citations": 4,
            "database": "openalex",
            "relation": "search",
        }],
        "paper_reference_excerpts": [],
        "provider_status": [
            {"provider": "OpenAlex", "query": "q", "ok": True, "result_count": 1, "error": ""}
        ],
    }
    searches = 0
    prompts: list[str] = []

    async def collect(*_args, **_kwargs):
        nonlocal searches
        searches += 1
        return evidence

    async def complete(_system, user, **_kwargs):
        prompts.append(user)
        return "Report citing [S1]"

    monkeypatch.setattr(manager.deep_research, "collect_external_evidence", collect)
    monkeypatch.setattr(manager, "text_complete", complete)
    try:
        result = asyncio.run(
            manager.run_plugin(paper_id, "gloss.deep-research", refresh=True)
        )
        cached = manager.get_cached_result(paper_id, "gloss.deep-research")

        assert searches == 1
        assert "=== VERIFIED NETWORK RESEARCH ===" in prompts[0]
        assert "https://example.org/retrieved" in prompts[0]
        assert result["research"]["source_count"] == 1
        assert cached == result
    finally:
        client.delete("/api/plugins/gloss.deep-research")


def test_deep_research_network_failure_stops_before_ai(client, paper_id, monkeypatch):
    from app.plugins import manager

    assert client.post("/api/plugins/gloss.deep-research/install").status_code == 200
    ai_calls = 0

    async def fail_search(*_args, **_kwargs):
        raise manager.deep_research.DeepResearchNetworkError("network unavailable")

    async def must_not_complete(*_args, **_kwargs):
        nonlocal ai_calls
        ai_calls += 1
        return "fabricated"

    monkeypatch.setattr(manager.deep_research, "collect_external_evidence", fail_search)
    monkeypatch.setattr(manager, "text_complete", must_not_complete)
    try:
        with pytest.raises(manager.deep_research.DeepResearchNetworkError):
            asyncio.run(
                manager.run_plugin(paper_id, "gloss.deep-research", refresh=True)
            )
        assert ai_calls == 0
    finally:
        client.delete("/api/plugins/gloss.deep-research")


def test_upgrade_installed_marketplace_plugins_is_first_party_only(tmp_path, monkeypatch):
    from app.plugins import manager

    monkeypatch.setattr(manager, "PLUGINS_DIR", tmp_path)
    deleted: list[str] = []
    monkeypatch.setattr(manager.store, "cache_delete_prefix", deleted.append)
    old = manager.validate_manifest({
        **next(item for item in manager.MARKETPLACE if item["id"] == "gloss.deep-research"),
        "version": "1.0.0",
        "permissions": ["paper:read", "ai:complete"],
        "prompt": "Old local-only prompt.",
    })
    custom = manager.validate_manifest({
        "id": "example.custom",
        "api_version": 1,
        "name": "Custom",
        "version": "9.0.0",
        "author": "User",
        "description": "Must remain untouched.",
        "permissions": ["paper:read", "ai:complete"],
        "prompt": "Custom prompt.",
    })
    manager._write_manifest(old)
    manager._write_manifest(custom)

    assert manager.upgrade_installed_marketplace_plugins() == ["gloss.deep-research"]
    assert manager.get_installed("gloss.deep-research")["version"] == "1.1.0"
    assert manager.get_installed("example.custom")["prompt"] == "Custom prompt."
    assert deleted == ["plugin:gloss.deep-research:"]


@pytest.mark.parametrize(
    ("requirement", "text", "expected_reason"),
    [
        ("formulas", "A qualitative user study with no mathematical notation.", "no_formulas"),
        ("method_content", "This is neutral background prose without procedure details.", "no_method_content"),
        ("claims_or_evidence", "Neutral background prose without reported findings.", "no_claims_or_evidence"),
        ("terms", "short text", "no_terms"),
        ("figures_or_tables", "Narrative only; no visual material is included.", "no_figures_or_tables"),
    ],
)
def test_unmet_plugin_requirement_returns_status_without_calling_provider(
    client, paper_id, monkeypatch, requirement, text, expected_reason
):
    """The route must short-circuit before registry.complete/provider execution."""
    from app.plugins import manager
    from app.providers import registry

    plugin_id = f"example.preflight-{requirement.replace('_', '-')}"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Preflight Test",
        "version": "1.0.0",
        "author": "Test",
        "description": "Verifies declarative plugin preflight.",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": [requirement],
        "prompt": "This must not reach a provider.",
    }
    assert client.post("/api/plugins/install", json={"manifest": manifest}).status_code == 200
    monkeypatch.setattr(manager.store, "load_parsed", lambda _paper_id: {"full_text": text, "pages": []})
    calls = 0

    async def provider_must_not_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider should not be called for unmet plugin requirements")

    monkeypatch.setattr(registry, "complete", provider_must_not_run)
    try:
        response = client.post(f"/api/plugins/{plugin_id}/papers/{paper_id}/run", json={})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "status": "unavailable",
            "reason": {"code": expected_reason, "requirement": requirement},
            "markdown": "",
        }
        assert calls == 0
    finally:
        client.delete(f"/api/plugins/{plugin_id}")


def test_plugin_agent_and_configuration_are_declarative_and_defaulted(client):
    plugin_id = "example.configurable-review"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Configurable Review",
        "version": "1.0.0",
        "author": "Test",
        "description": "Exercises host-rendered settings.",
        "permissions": ["paper:read", "ai:complete"],
        "agent": {
            "name": "Evidence Scout",
            "icon": "🔎",
            "messages": {"thinking": "Checking every claim"},
        },
        "contributes": {
            "paper_sidebar": {"tab_name": "Configured"},
            "configuration": {
                "title": "Configurable Review",
                "properties": {
                    f"{plugin_id}.strictness": {
                        "type": "string",
                        "enum": ["balanced", "strict"],
                        "enumDescriptions": ["Major gaps", "Every material gap"],
                        "default": "balanced",
                        "description": "Review strictness.",
                        "order": 10,
                    },
                    "includeFollowUps": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include follow-up experiments.",
                        "order": 20,
                    },
                },
            },
        },
        "prompt": "Review the supplied paper.",
    }
    try:
        response = client.post("/api/plugins/install", json={"manifest": manifest})
        assert response.status_code == 200, response.text
        installed = response.json()
        assert installed["agent"]["name"] == "Evidence Scout"
        assert installed["agent"]["name_zh"] == "研究助手"
        assert installed["agent"]["messages"]["thinking"] == "Checking every claim"
        assert installed["agent"]["messages"]["writing"] == "Writing the result"
        properties = installed["contributes"]["configuration"]["properties"]
        assert set(properties) == {"strictness", "includeFollowUps"}
        assert properties["strictness"]["default"] == "balanced"
    finally:
        client.delete(f"/api/plugins/{plugin_id}")


@pytest.mark.parametrize(
    "properties",
    [
        {
            "mode": {"type": "string", "default": "normal"},
            "mode.detail": {"type": "boolean", "default": True},
        },
        {"count": {"type": "integer", "default": "not-an-integer"}},
        {"unknown": {"type": "object", "default": {}}},
        {"missingDefault": {"type": "boolean"}},
        {"pattern": {"type": "string", "default": "ok", "pattern": "["}},
        {
            "range": {
                "type": "number",
                "default": 2,
                "minimum": 3,
                "maximum": 1,
            }
        },
        {"length": {"type": "string", "default": "ok", "minLength": "1"}},
    ],
)
def test_invalid_plugin_configuration_schema_is_rejected(client, properties):
    plugin_id = "example.invalid-configuration"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Invalid Configuration",
        "version": "1.0.0",
        "description": "Must fail validation.",
        "permissions": ["paper:read", "ai:complete"],
        "contributes": {
            "paper_sidebar": {"tab_name": "Invalid"},
            "configuration": {"properties": properties},
        },
        "prompt": "Review the paper.",
    }
    response = client.post("/api/plugins/install", json={"manifest": manifest})
    assert response.status_code == 400


def test_plugin_configuration_reaches_prompt_and_partitions_cache(
    client, paper_id, monkeypatch
):
    from app.plugins import manager

    plugin_id = "example.prompt-settings"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Prompt Settings",
        "version": "1.0.0",
        "description": "Passes validated settings to the prompt.",
        "permissions": ["paper:read", "ai:complete"],
        "contributes": {
            "paper_sidebar": {"tab_name": "Prompt"},
            "configuration": {
                "properties": {
                    "strictness": {
                        "type": "string",
                        "enum": ["balanced", "strict"],
                        "default": "balanced",
                    }
                }
            },
        },
        "prompt": "Review the paper.",
    }
    assert client.post("/api/plugins/install", json={"manifest": manifest}).status_code == 200
    prompts: list[str] = []
    cache_keys: list[str] = []

    async def fake_complete(_system, user, **_kwargs):
        prompts.append(user)
        return "configured result"

    def capture_cache(_paper_id, key, _value):
        cache_keys.append(key)

    monkeypatch.setattr(manager, "text_complete", fake_complete)
    monkeypatch.setattr(manager.store, "cache_set", capture_cache)
    try:
        asyncio.run(
            manager.run_plugin(
                paper_id, plugin_id, refresh=True,
                configuration={"strictness": "strict"},
            )
        )
        asyncio.run(
            manager.run_plugin(
                paper_id, plugin_id, refresh=True,
                configuration={"strictness": "balanced"},
            )
        )
        assert '"strictness": "strict"' in prompts[0]
        assert "=== PLUGIN SETTINGS ===" in prompts[0]
        assert cache_keys[0] != cache_keys[1]
        with pytest.raises(ValueError, match="invalid value"):
            asyncio.run(
                manager.run_plugin(
                    paper_id, plugin_id, refresh=True,
                    configuration={"strictness": "unsupported"},
                )
            )
    finally:
        client.delete(f"/api/plugins/{plugin_id}")


def test_plugins_inherit_reading_lens_guidance_and_cache_partition(
    client, paper_id, monkeypatch
):
    from app.plugins import manager

    plugin_id = "example.discipline-aware"
    manifest = {
        "id": plugin_id,
        "api_version": 1,
        "name": "Discipline Aware",
        "version": "1.0.0",
        "description": "Checks the shared reading lens.",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text"],
        "prompt": "Analyze this paper in depth.",
    }
    assert client.post("/api/plugins/install", json={"manifest": manifest}).status_code == 200
    systems: list[str] = []
    cache_keys: list[str] = []

    async def fake_complete(system, _user, **_kwargs):
        systems.append(system)
        return "result"

    def capture_cache(_paper_id, key, _value):
        cache_keys.append(key)

    monkeypatch.setattr(manager, "text_complete", fake_complete)
    monkeypatch.setattr(manager.store, "cache_set", capture_cache)
    try:
        client.patch(f"/api/papers/{paper_id}", json={"reading_lens": "humanities"})
        asyncio.run(manager.run_plugin(paper_id, plugin_id, refresh=True))
        client.patch(f"/api/papers/{paper_id}", json={"reading_lens": "stem"})
        asyncio.run(manager.run_plugin(paper_id, plugin_id, refresh=True))

        assert "primary and secondary sources" in systems[0]
        assert "Do not demand experiments" in systems[0]
        assert "evaluation metrics" in systems[1]
        assert cache_keys[0] != cache_keys[1]
    finally:
        client.delete(f"/api/plugins/{plugin_id}")
