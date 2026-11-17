"""Codex subscription-backed image generation using the built-in image_gen tool."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from ..config import ensure_codex_sandbox, load_config
from ..platform_support import (
    resolve_cli_command,
    subprocess_group_options,
    terminate_process_tree,
)
from .images import GeneratedImage, image_type

IMAGE_MODEL = "Codex image_gen"
_QUALITIES = {"low", "medium", "high"}


def codex_available() -> bool:
    """Return whether a safely resolvable Codex CLI is present on this host."""
    try:
        resolve_cli_command("codex")
    except (FileNotFoundError, RuntimeError):
        return False
    return True


def _rate_limited(detail: str) -> bool:
    lowered = detail.lower()
    return "429" in lowered or "too many requests" in lowered or "exceeded retry limit" in lowered


def _error_summary(stdout: bytes, stderr: bytes) -> str:
    detail = (stderr + b"\n" + stdout).decode(errors="replace")
    messages: list[str] = []
    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            messages.append(line)
            continue
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, str) and message:
            messages.append(message)
    return " | ".join(messages[-4:])[:1200]


def _last_message(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[:1200]
    except OSError:
        return ""


def _worker_prompt(image_prompt: str, destination: Path, quality: str) -> str:
    detail = {
        "low": "fast draft with a clean, readable composition",
        "medium": "polished educational visual with clear hierarchy",
        "high": "high-detail final educational visual with especially careful legibility",
    }[quality]
    return (
        "Use the installed imagegen skill and its default built-in tool mode. "
        "You are a single-image rendering worker. You MUST call the built-in image_gen tool exactly once "
        "for the image specification below. Do not use OPENAI_API_KEY, an Image API, scripts/image_gen.py, "
        "SVG, HTML, CSS, canvas, or a placeholder. If image_gen is unavailable or fails, fail honestly and "
        "do not create any substitute image.\n\n"
        f"Rendering target: {detail}.\n"
        f"Image specification:\n{image_prompt}\n\n"
        "After image_gen succeeds, copy the exact generated raster bitmap from Codex's generated_images "
        f"location to this exact workspace path: {destination}\n"
        "Do not edit or recreate the bitmap during the copy. Finish only after that file exists."
    )


async def _generate_one(prompt: str, quality: str, index: int) -> GeneratedImage:
    sandbox = ensure_codex_sandbox()
    sandbox.mkdir(parents=True, exist_ok=True)
    cfg = load_config().get("providers", {}).get("local_codex", {})
    timeout = max(float(cfg.get("timeout") or 600), 180.0)

    with tempfile.TemporaryDirectory(prefix=f"ve-frame-{index:02d}-", dir=str(sandbox)) as raw_job:
        job = Path(raw_job).resolve()
        destination = job / "visual-output.png"
        result_path = job / "codex-result.txt"
        command = resolve_cli_command("codex") + [
            "--enable", "image_generation",
            "exec",
            "--skip-git-repo-check",
            "-s", "workspace-write",
            "-C", str(job),
            "-o", str(result_path),
            "--ephemeral",
            "-",
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **subprocess_group_options(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(_worker_prompt(prompt, destination, quality).encode("utf-8")),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                await terminate_process_tree(process)
                raise RuntimeError(f"Codex image_gen timed out after {timeout:g}s")
            if process.returncode != 0:
                detail = _error_summary(stdout, stderr)
                if _rate_limited(detail):
                    raise RuntimeError(
                        "Codex image_gen was rate-limited (HTTP 429). Retry later, or explicitly select "
                        "OpenAI gpt-image-2 after configuring an API key."
                    )
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(f"Codex image_gen failed{suffix}")
            if not destination.is_file():
                detail = _last_message(result_path) or _error_summary(stdout, stderr)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(
                    "Codex image_gen completed without copying a generated bitmap into Gloss"
                    f"{suffix}"
                )
            content = destination.read_bytes()
            extension, media_type = image_type(content)
            return GeneratedImage(content, extension, media_type)
        finally:
            if process is not None and process.returncode is None:
                await terminate_process_tree(process)


async def generate_images(
    prompts: list[str],
    *,
    quality: str = "medium",
) -> list[GeneratedImage]:
    """Generate one independent built-in image_gen result for each prompt."""
    if not prompts or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts):
        raise ValueError("at least one non-empty image prompt is required")
    if quality not in _QUALITIES:
        raise ValueError(f"unsupported image quality: {quality}")
    limiter = asyncio.Semaphore(2)

    async def limited(prompt: str, index: int) -> GeneratedImage:
        async with limiter:
            return await _generate_one(prompt, quality, index)

    return list(await asyncio.gather(*(
        limited(prompt, index) for index, prompt in enumerate(prompts, start=1)
    )))
