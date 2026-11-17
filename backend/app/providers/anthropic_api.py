"""Anthropic Messages API provider (needs an API key)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import load_config
from .base import Message, Provider, anthropic_usage, normalize_usage, report_stream_usage


def _cfg() -> dict:
    return load_config()["providers"]["anthropic"]


class AnthropicProvider(Provider):
    name = "anthropic"

    def _headers(self) -> dict:
        return {
            "x-api-key": _cfg().get("api_key", ""),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _body(self, system: str, messages: list[Message], model: str | None, stream: bool) -> dict:
        c = _cfg()
        msgs = []
        for message in messages:
            if message.get("role") in ("system", "developer"):
                continue
            text = message.get("content") or ""
            attachments = message.get("attachments") or []
            content: str | list[dict] = text
            if attachments and message.get("role") != "assistant":
                content = []
                for attachment in attachments:
                    data_url = attachment.get("image_data_url") or ""
                    if not data_url.startswith("data:image/png;base64,"):
                        continue
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": data_url.split(",", 1)[1],
                        },
                    })
                content.append({"type": "text", "text": text})
            msgs.append({
                "role": "assistant" if message.get("role") == "assistant" else "user",
                "content": content,
            })
        body = {
            "model": model or c.get("model", "claude-sonnet-4-5"),
            "max_tokens": 4096,
            "messages": msgs,
            "stream": stream,
        }
        if system:
            # Separate tool appendix gets its own breakpoint; no native tools
            # are sent because GlossX executes its text protocol under approval.
            head, marker, tail = system.partition("# Tools\n")
            body["system"] = [{"type": "text", "text": head, "cache_control": {"type": "ephemeral"}}] if head.strip() else []
            if marker:
                # Tools/skills/subagents end before project instruction blocks.
                boundaries = [tail.find(label) for label in ("\n\n# Global instructions", "\n\n# Project instructions", "\n\n# Project\n") if label in tail]
                boundary = min(boundaries) if boundaries else len(tail)
                body["system"].append({"type": "text", "text": marker + tail[:boundary], "cache_control": {"type": "ephemeral"}})
                if tail[boundary:]:
                    body["system"].append({"type": "text", "text": tail[boundary:], "cache_control": {"type": "ephemeral"}})
        if len(msgs) > 1:
            stable = msgs[-2]
            if isinstance(stable["content"], str):
                stable["content"] = [{"type": "text", "text": stable["content"]}]
            if stable["content"]:
                stable["content"][-1]["cache_control"] = {"type": "ephemeral"}
        return body

    async def complete(self, system, messages, model=None):
        c = _cfg()
        async with httpx.AsyncClient(timeout=c.get("timeout", 300), trust_env=False) as client:
            r = await client.post(
                f"{c.get('base_url', 'https://api.anthropic.com').rstrip('/')}/v1/messages",
                headers=self._headers(),
                json=self._body(system, messages, model, stream=False),
            )
            r.raise_for_status()
            data = r.json()
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        u = data.get("usage", {})
        usage = normalize_usage(anthropic_usage(u), system, messages, text)
        return text, usage

    async def stream(self, system, messages, model=None) -> AsyncIterator[str]:
        c = _cfg()
        usage = {}
        async with httpx.AsyncClient(timeout=c.get("timeout", 300), trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{c.get('base_url', 'https://api.anthropic.com').rstrip('/')}/v1/messages",
                headers=self._headers(),
                json=self._body(system, messages, model, stream=True),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "message_start":
                        usage.update((evt.get("message") or {}).get("usage") or {})
                    elif evt.get("type") == "message_delta":
                        usage.update(evt.get("usage") or {})
                    if usage:
                        report_stream_usage({**anthropic_usage(usage), "_complete": evt.get("type") == "message_stop"})
                    if evt.get("type") == "content_block_delta":
                        d = evt.get("delta", {})
                        if d.get("type") == "text_delta":
                            yield d.get("text", "")
