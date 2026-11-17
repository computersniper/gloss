"""Visual Explainer manifest, real image transport, assets, and cache boundaries."""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest


_PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def test_visual_explainer_marketplace_contract(client):
    snapshot = client.get("/api/plugins").json()
    plugin = next(item for item in snapshot["marketplace"] if item["id"] == "gloss.visual-explainer")
    assert plugin["output"] == "visual"
    assert plugin["permissions"] == ["paper:read", "ai:complete", "image:generate"]
    assert plugin["contributes"]["configuration"]["properties"]["style"]["enum"] == [
        "diagram", "animated", "teacher",
    ]
    assert plugin["contributes"]["configuration"]["properties"]["imageProvider"]["enum"] == [
        "auto", "codex", "openai",
    ]
    assert plugin["contributes"]["configuration"]["properties"]["style"]["enumItemLabels_zh"] == [
        "示意图", "动画式讲解", "老师风格讲解",
    ]
    assert plugin["agent"]["messages"]["thinking"]["zh"] == "正在规划图像讲解"


def test_image_generation_requires_a_real_openai_api_key(monkeypatch):
    from app.providers import openai_images

    monkeypatch.setattr(openai_images, "load_config", lambda: {
        "providers": {"openai": {"api_key": "", "base_url": "https://api.openai.com/v1"}}
    })
    with pytest.raises(ValueError, match="OpenAI API key is required"):
        asyncio.run(openai_images.generate_images(["a grounded diagram"]))


def test_gpt_image_2_request_and_base64_decoding(monkeypatch):
    from app.providers import openai_images

    calls: list[dict] = []

    class FakeResponse:
        is_error = False

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(_PNG).decode("ascii")}]}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(openai_images, "load_config", lambda: {
        "providers": {"openai": {
            "api_key": "test-secret",
            "base_url": "https://api.openai.com/v1",
            "timeout": 10,
        }}
    })
    monkeypatch.setattr(openai_images.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(openai_images.generate_images(["frame one"], quality="high"))

    request = next(call for call in calls if "json" in call)
    assert request["url"] == "https://api.openai.com/v1/images/generations"
    assert request["json"] == {
        "model": "gpt-image-2",
        "prompt": "frame one",
        "size": "1536x1024",
        "quality": "high",
        "n": 1,
    }
    assert request["headers"]["Authorization"] == "Bearer test-secret"
    assert result[0].content == _PNG
    assert result[0].media_type == "image/png"


def test_codex_image_gen_uses_writable_ephemeral_job_and_validates_bitmap(
    tmp_path: Path, monkeypatch
):
    from app.providers import codex_images

    calls: list[dict] = []

    class Process:
        pid = 123
        returncode = 0

        async def communicate(self, prompt: bytes):
            call = calls[-1]
            call["prompt"] = prompt.decode("utf-8")
            args = call["args"]
            job = Path(args[args.index("-C") + 1])
            (job / "visual-output.png").write_bytes(_PNG)
            return b'{"type":"turn.completed"}\n', b""

    async def create(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Process()

    monkeypatch.setattr(codex_images, "ensure_codex_sandbox", lambda: tmp_path)
    monkeypatch.setattr(codex_images, "resolve_cli_command", lambda _name: ["codex"])
    monkeypatch.setattr(codex_images, "subprocess_group_options", lambda: {})
    monkeypatch.setattr(codex_images, "load_config", lambda: {
        "providers": {"local_codex": {"timeout": 300}}
    })
    monkeypatch.setattr(codex_images.asyncio, "create_subprocess_exec", create)

    result = asyncio.run(codex_images.generate_images(["grounded diagram"], quality="high"))

    args = calls[0]["args"]
    assert args[:4] == ("codex", "--enable", "image_generation", "exec")
    assert (args[args.index("-s")], args[args.index("-s") + 1]) == ("-s", "workspace-write")
    assert "--ephemeral" in args
    assert "built-in image_gen tool exactly once" in calls[0]["prompt"]
    assert "placeholder" in calls[0]["prompt"]
    assert result == [codex_images.GeneratedImage(_PNG, ".png", "image/png")]


def test_codex_image_gen_surfaces_rate_limit_without_fake_fallback(
    tmp_path: Path, monkeypatch
):
    from app.providers import codex_images

    class Process:
        pid = 123
        returncode = 1

        async def communicate(self, _prompt: bytes):
            return b'{"type":"error","message":"exceeded retry limit, last status: 429 Too Many Requests"}\n', b""

    monkeypatch.setattr(codex_images, "ensure_codex_sandbox", lambda: tmp_path)
    monkeypatch.setattr(codex_images, "resolve_cli_command", lambda _name: ["codex"])
    monkeypatch.setattr(codex_images, "subprocess_group_options", lambda: {})
    monkeypatch.setattr(codex_images, "load_config", lambda: {"providers": {"local_codex": {}}})
    monkeypatch.setattr(
        codex_images.asyncio, "create_subprocess_exec", lambda *_args, **_kwargs: _async_value(Process())
    )

    with pytest.raises(RuntimeError, match=r"rate-limited \(HTTP 429\)"):
        asyncio.run(codex_images.generate_images(["frame one"]))


def test_codex_image_gen_reports_last_message_when_no_bitmap_is_copied(
    tmp_path: Path, monkeypatch
):
    from app.providers import codex_images

    class Process:
        pid = 123
        returncode = 0

        def __init__(self, args):
            self.args = args

        async def communicate(self, _prompt: bytes):
            result = Path(self.args[self.args.index("-o") + 1])
            result.write_text("image_gen is unavailable in this session", encoding="utf-8")
            return b"", b""

    async def create(*args, **_kwargs):
        return Process(args)

    monkeypatch.setattr(codex_images, "ensure_codex_sandbox", lambda: tmp_path)
    monkeypatch.setattr(codex_images, "resolve_cli_command", lambda _name: ["codex"])
    monkeypatch.setattr(codex_images, "subprocess_group_options", lambda: {})
    monkeypatch.setattr(codex_images, "load_config", lambda: {"providers": {"local_codex": {}}})
    monkeypatch.setattr(codex_images.asyncio, "create_subprocess_exec", create)

    with pytest.raises(RuntimeError, match="image_gen is unavailable in this session"):
        asyncio.run(codex_images.generate_images(["frame one"]))


async def _async_value(value):
    return value


def test_animated_visual_plan_generates_four_assets_and_uses_reading_lens(
    client, monkeypatch
):
    from app.plugins import visual_explainer
    from app.providers.openai_images import GeneratedImage

    observed: dict[str, str | list[str]] = {}

    async def fake_plan(system, user, _shape, **_kwargs):
        observed["system"] = system
        observed["user"] = user
        return {
            "title": "A four-step interpretation",
            "overview": "The paper moves from context to a qualified conclusion.",
            "visual_language": "Editorial collage with a consistent indigo thread",
            "frames": [
                {
                    "heading": f"Step {index}",
                    "caption": f"Grounded explanation {index}",
                    "alt_text": f"Frame {index} of the paper explanation",
                    "image_prompt": f"Show paper-grounded stage {index}",
                }
                for index in range(1, 5)
            ],
        }

    async def fake_images(prompts, **_kwargs):
        observed["prompts"] = prompts
        return [GeneratedImage(_PNG + bytes([index]), ".png", "image/png") for index in range(4)]

    monkeypatch.setattr(visual_explainer, "json_complete", fake_plan)
    monkeypatch.setattr(visual_explainer.codex_images, "generate_images", fake_images)
    response = asyncio.run(visual_explainer.run(
        paper_id="paper-visual-test",
        plugin_id="gloss.visual-explainer",
        paper_text="A humanities paper grounded in primary source interpretation.",
        language="中文 (Simplified Chinese)",
        style="animated",
        quality="medium",
        image_provider="codex",
        lens_guidance="Use primary and secondary sources and preserve historical context.",
        cache_revision="animated-cache",
        provider="local_codex",
        model=None,
    ))

    visual = response["visual_explanation"]
    assert visual["style"] == "animated"
    assert visual["image_provider"] == "codex"
    assert visual["model"] == "Codex image_gen"
    assert visual["frame_count"] == 4
    assert len(visual["images"]) == 4
    assert len(observed["prompts"]) == 4
    assert "primary and secondary sources" in observed["system"]
    assert visual_explainer.assets_available(visual)
    asset = client.get(visual["images"][0]["url"])
    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/png"


def test_visual_cache_partitions_style_quality_and_uninstall_cleans_assets(client):
    from app.plugins import manager, visual_explainer
    from app.providers.openai_images import GeneratedImage

    manifest = manager.install_marketplace("gloss.visual-explainer")
    try:
        diagram_key, _ = manager._plugin_cache_key(
            manifest, "zh", {"imageProvider": "codex", "style": "diagram", "quality": "medium"}
        )
        animated_key, _ = manager._plugin_cache_key(
            manifest, "zh", {"imageProvider": "codex", "style": "animated", "quality": "medium"}
        )
        high_key, _ = manager._plugin_cache_key(
            manifest, "zh", {"imageProvider": "codex", "style": "diagram", "quality": "high"}
        )
        openai_key, _ = manager._plugin_cache_key(
            manifest, "zh", {"imageProvider": "openai", "style": "diagram", "quality": "medium"}
        )
        assert len({diagram_key, animated_key, high_key, openai_key}) == 4

        stored = visual_explainer._save_assets(
            "cleanup-paper", manifest["id"], diagram_key,
            [GeneratedImage(_PNG, ".png", "image/png")],
        )
        path = visual_explainer.resolve_asset_path(
            "cleanup-paper", manifest["id"], stored[0]["url"].rsplit("/", 1)[-1]
        )
        assert path.is_file()
    finally:
        manager.uninstall(manifest["id"])
    assert not path.exists()


@pytest.mark.parametrize("filename", ["../secret.png", "..%2Fsecret.png", "notes.txt"])
def test_visual_asset_path_rejects_traversal_and_non_images(filename):
    from app.plugins import visual_explainer

    with pytest.raises(ValueError):
        visual_explainer.resolve_asset_path("paper", "gloss.visual-explainer", filename)
