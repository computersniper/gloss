"""Shared image payload validation for host-controlled image providers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    extension: str
    media_type: str


def image_type(content: bytes) -> tuple[str, str]:
    """Identify supported raster images from magic bytes, never extensions."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise ValueError("generated image was not a supported PNG, JPEG, or WebP file")
