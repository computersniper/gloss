"""Host-controlled OpenAI image generation for trusted Gloss features."""
from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

import httpx

from ..config import load_config
from .images import GeneratedImage, image_type

IMAGE_MODEL = "gpt-image-2"
_SIZES = {"1024x1024", "1536x1024", "1024x1536"}
_QUALITIES = {"low", "medium", "high"}


def _error_detail(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip()[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
    return str(payload)[:300]


def image_api_settings() -> tuple[str, str, float]:
    """Return validated host image settings without exposing the API key."""
    cfg = load_config().get("providers", {}).get("openai", {})
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(
            "OpenAI API key is required when the Visual Explainer image backend "
            "is set to OpenAI gpt-image-2"
        )
    base_url = str(cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    timeout = max(float(cfg.get("timeout") or 300), 180.0)
    return base_url, api_key, timeout


async def generate_images(
    prompts: list[str],
    *,
    size: str = "1536x1024",
    quality: str = "medium",
) -> list[GeneratedImage]:
    """Generate one real image for each prompt with the OpenAI Image API."""
    if not prompts or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts):
        raise ValueError("at least one non-empty image prompt is required")
    if size not in _SIZES:
        raise ValueError(f"unsupported image size: {size}")
    if quality not in _QUALITIES:
        raise ValueError(f"unsupported image quality: {quality}")

    base_url, api_key, timeout = image_api_settings()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    limiter = asyncio.Semaphore(2)

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async def generate_one(prompt: str) -> GeneratedImage:
            async with limiter:
                response = await client.post(
                    f"{base_url}/images/generations",
                    headers=headers,
                    json={
                        "model": IMAGE_MODEL,
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "n": 1,
                    },
                )
            if response.is_error:
                detail = _error_detail(response)
                suffix = f": {detail}" if detail else ""
                raise ValueError(f"OpenAI Image API failed with HTTP {response.status_code}{suffix}")
            try:
                encoded = response.json()["data"][0]["b64_json"]
                content = base64.b64decode(encoded, validate=True)
            except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as error:
                raise ValueError("OpenAI Image API returned invalid base64 image data") from error
            try:
                extension, media_type = image_type(content)
            except ValueError as error:
                raise ValueError(
                    "OpenAI image response was not a supported PNG, JPEG, or WebP file"
                ) from error
            return GeneratedImage(content, extension, media_type)

        return list(await asyncio.gather(*(generate_one(prompt) for prompt in prompts)))
