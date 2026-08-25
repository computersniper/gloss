"""Paper-grounded visual explanation planning, generation, and asset storage."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from ..config import PLUGIN_OUTPUTS_DIR
from ..features.common import json_complete
from ..providers import codex_images, openai_images
from ..providers.images import GeneratedImage

PLUGIN_ID = "gloss.visual-explainer"
IMAGE_PIPELINE_REVISION = "visual-explainer-v2:codex-image_gen:gpt-image-2"
STYLES = {"diagram", "animated", "teacher"}
QUALITIES = {"low", "medium", "high"}
IMAGE_PROVIDERS = {"auto", "codex", "openai"}
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _clean(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"visual plan requires {field}")
    return value.strip()[:maximum]


def _normalize_plan(payload: Any, style: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("visual planning model did not return an object")
    expected_frames = 4 if style == "animated" else 1
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or len(raw_frames) != expected_frames:
        raise ValueError(f"visual plan requires exactly {expected_frames} frame(s) for {style}")
    frames = []
    for index, raw in enumerate(raw_frames, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"visual plan frame {index} must be an object")
        frames.append({
            "heading": _clean(raw.get("heading"), f"frames[{index}].heading", 120),
            "caption": _clean(raw.get("caption"), f"frames[{index}].caption", 500),
            "alt_text": _clean(raw.get("alt_text"), f"frames[{index}].alt_text", 300),
            "image_prompt": _clean(raw.get("image_prompt"), f"frames[{index}].image_prompt", 2400),
        })
    return {
        "title": _clean(payload.get("title"), "title", 160),
        "overview": _clean(payload.get("overview"), "overview", 1200),
        "visual_language": _clean(payload.get("visual_language"), "visual_language", 500),
        "frames": frames,
    }


def _planning_prompt(style: str, language: str, lens_guidance: str) -> tuple[str, str]:
    style_instruction = {
        "diagram": (
            "Plan one precise landscape explanatory diagram: show the paper's central concepts, "
            "relationships, evidence, or method with a restrained academic infographic composition."
        ),
        "animated": (
            "Plan exactly four sequential landscape storyboard frames. Each frame must advance the "
            "same explanation, use consistent visual characters/colors/layout, and remain intelligible "
            "when played as a simple frame-by-frame animation."
        ),
        "teacher": (
            "Plan one landscape teaching scene in which an approachable instructor uses a board and "
            "simple visual props to explain the paper's core idea accurately."
        ),
    }[style]
    system = (
        "You are the visual planning stage of Gloss Visual Explainer. Ground every claim in the supplied "
        "paper. Never invent findings, numbers, quotations, datasets, causal links, author intent, or "
        "historical facts. Prefer concepts and relationships over long text because image models render "
        "small text unreliably. Make labels short and spell them exactly. Adapt the visual logic to the "
        "discipline instead of forcing every paper into an engineering pipeline. "
        f"Write plan text in {language}. {lens_guidance}"
    )
    user = (
        f"STYLE: {style}\n{style_instruction}\n\n"
        "Return a visual plan with title, overview, visual_language, and frames. Each frame needs heading, "
        "caption, alt_text, and a standalone image_prompt. The image_prompt must describe only visible "
        "content and explicitly preserve factual uncertainty.\n\n=== PAPER ===\n"
    )
    return system, user


def _image_prompt(plan: dict[str, Any], frame: dict[str, str], style: str, index: int) -> str:
    style_rules = {
        "diagram": "Clean scholarly infographic, crisp hierarchy, simple arrows, minimal readable labels.",
        "animated": (
            f"Educational storyboard frame {index} of 4, consistent palette and recurring visual motifs "
            "across the sequence, clear sense of progression."
        ),
        "teacher": "Warm teacher-led whiteboard explanation, accessible but academically serious.",
    }[style]
    return (
        f"Create a 3:2 landscape explanatory image. {style_rules}\n"
        f"Shared visual language: {plan['visual_language']}\n"
        f"Visible content: {frame['image_prompt']}\n"
        "Accuracy constraints: do not add statistics, citations, equations, labels, people, or conclusions "
        "not explicitly requested above. Avoid decorative pseudo-text, watermarks, logos, and UI chrome. "
        "Use short legible labels only."
    )


def _asset_root(paper_id: str, plugin_id: str) -> Path:
    if not _COMPONENT.fullmatch(paper_id) or not _COMPONENT.fullmatch(plugin_id):
        raise ValueError("invalid visual asset identifier")
    root = PLUGIN_OUTPUTS_DIR.resolve()
    target = (root / paper_id / plugin_id).resolve()
    if root not in target.parents:
        raise ValueError("visual asset path escaped the plugin output directory")
    return target


def _save_assets(
    paper_id: str,
    plugin_id: str,
    revision: str,
    generated: list[GeneratedImage],
) -> list[dict[str, str]]:
    root = _asset_root(paper_id, plugin_id)
    root.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(revision.encode("utf-8")).hexdigest()[:20]
    saved = []
    for index, image in enumerate(generated, start=1):
        filename = f"{stem}-{index:02d}{image.extension}"
        destination = root / filename
        temporary = root / f".{filename}.tmp"
        temporary.write_bytes(image.content)
        temporary.replace(destination)
        saved.append({
            "url": f"/api/plugins/assets/{paper_id}/{plugin_id}/{filename}",
            "media_type": image.media_type,
        })
    return saved


def resolve_asset_path(paper_id: str, plugin_id: str, filename: str) -> Path:
    if not _COMPONENT.fullmatch(filename) or Path(filename).suffix.lower() not in {".png", ".jpg", ".webp"}:
        raise ValueError("invalid visual asset filename")
    root = _asset_root(paper_id, plugin_id)
    target = (root / filename).resolve()
    if target.parent != root or not target.is_file():
        raise ValueError("visual asset was not found")
    return target


def assets_available(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    images = metadata.get("images")
    if not isinstance(images, list) or not images:
        return False
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("url"), str):
            return False
        parts = image["url"].split("/")
        if len(parts) != 7 or parts[1:4] != ["api", "plugins", "assets"]:
            return False
        try:
            resolve_asset_path(parts[4], parts[5], parts[6])
        except ValueError:
            return False
    return True


def remove_plugin_assets(plugin_id: str) -> None:
    if not _COMPONENT.fullmatch(plugin_id):
        raise ValueError("invalid plugin id")
    root = PLUGIN_OUTPUTS_DIR.resolve()
    if not root.is_dir():
        return
    for paper_root in root.iterdir():
        if not paper_root.is_dir():
            continue
        target = (paper_root / plugin_id).resolve()
        if root not in target.parents or target.parent != paper_root.resolve():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        try:
            paper_root.rmdir()
        except OSError:
            pass


def _markdown(plan: dict[str, Any], style: str, language: str) -> str:
    zh = "中文" in language or "Chinese" in language
    label = "分镜讲解" if zh and style == "animated" else "讲解要点" if zh else "Storyboard" if style == "animated" else "Explanation"
    lines = [f"# {plan['title']}", "", plan["overview"], "", f"## {label}", ""]
    for index, frame in enumerate(plan["frames"], start=1):
        prefix = f"{index}. " if style == "animated" else ""
        lines.extend([f"### {prefix}{frame['heading']}", "", frame["caption"], ""])
    return "\n".join(lines).strip()


def _resolve_image_provider(requested: str) -> str:
    if requested not in IMAGE_PROVIDERS:
        raise ValueError(f"unsupported image provider: {requested}")
    if requested != "auto":
        return requested
    if codex_images.codex_available():
        return "codex"
    try:
        openai_images.image_api_settings()
    except ValueError as error:
        raise ValueError(
            "Visual Explainer could not find a Codex CLI for built-in image_gen, and the "
            "OpenAI fallback has no configured API key"
        ) from error
    return "openai"


async def run(
    *,
    paper_id: str,
    plugin_id: str,
    paper_text: str,
    language: str,
    style: str,
    quality: str,
    image_provider: str,
    lens_guidance: str,
    cache_revision: str,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    if style not in STYLES:
        raise ValueError(f"unsupported visual explanation style: {style}")
    if quality not in QUALITIES:
        raise ValueError(f"unsupported visual explanation quality: {quality}")
    resolved_image_provider = _resolve_image_provider(image_provider)
    if resolved_image_provider == "openai":
        # Explicitly validate paid API configuration before spending a planning call.
        openai_images.image_api_settings()
    system, user_prefix = _planning_prompt(style, language, lens_guidance)
    shape = (
        '{"title":"...","overview":"...","visual_language":"...","frames":['
        '{"heading":"...","caption":"...","alt_text":"...","image_prompt":"..."}]}'
    )
    raw_plan = await json_complete(
        system,
        user_prefix + paper_text,
        shape,
        provider=provider,
        model=model,
        retries=1,
    )
    plan = _normalize_plan(raw_plan, style)
    prompts = [
        _image_prompt(plan, frame, style, index)
        for index, frame in enumerate(plan["frames"], start=1)
    ]
    if resolved_image_provider == "codex":
        generated = await codex_images.generate_images(prompts, quality=quality)
        image_model = codex_images.IMAGE_MODEL
    else:
        generated = await openai_images.generate_images(prompts, quality=quality)
        image_model = openai_images.IMAGE_MODEL
    stored = _save_assets(paper_id, plugin_id, cache_revision, generated)
    images = [
        {
            **asset,
            "alt": frame["alt_text"],
            "caption": frame["caption"],
            "heading": frame["heading"],
            "frame": index,
        }
        for index, (asset, frame) in enumerate(zip(stored, plan["frames"], strict=True), start=1)
    ]
    return {
        "status": "ready",
        "reason": None,
        "markdown": _markdown(plan, style, language),
        "visual_explanation": {
            "style": style,
            "image_provider": resolved_image_provider,
            "model": image_model,
            "quality": quality,
            "frame_count": len(images),
            "images": images,
        },
    }
