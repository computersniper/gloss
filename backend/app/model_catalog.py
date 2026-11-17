"""Model suggestions, not an account entitlement or validation allow-list.

Source: https://learn.chatgpt.com/docs/models (checked 2026-10-10).
Custom providers and saved model ids remain valid even if not listed here.
"""
from __future__ import annotations

_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_CURRENT = [
    ("gpt-6.1-sol", "GPT-6.1 Sol"),
    ("gpt-6-astra", "GPT-6 Astra"),
    ("gpt-6-luna", "GPT-6 Luna"),
    ("gpt-6-sol", "GPT-6 Sol"),
]
_COMPATIBLE = [
    ("gpt-5.6-sol", "GPT-5.6 Sol"),
    ("gpt-5.6-terra", "GPT-5.6 Terra"),
    ("gpt-5.6-luna", "GPT-5.6 Luna"),
]


# Claude Code accepts its aliases as well as full model ids.
_CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_CLAUDE_CLI = [
    ("opus", "Opus (latest)"),
    ("sonnet", "Sonnet (latest)"),
    ("haiku", "Haiku (latest)"),
]
_CLAUDE_IDS = [
    ("claude-fable-5-1", "Claude Fable 5.1"),
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
]


def _custom(provider: str) -> list[dict]:
    """Model ids the user added under Settings → Models (``providers.<name>.models``)."""
    from .config import load_config

    saved = (load_config().get("providers", {}).get(provider) or {}).get("models") or []
    return [{"id": str(m).strip(), "label": str(m).strip(), "custom": True}
            for m in saved if isinstance(m, str) and str(m).strip()][:50]


def _with_custom(provider: str, entries: list[dict]) -> list[dict]:
    known = {e["id"] for e in entries}
    return [*entries, *(e for e in _custom(provider) if e["id"] not in known)]


def suggestions() -> dict:
    entries = [
        {"id": model_id, "label": label, "reasoning_efforts": list(_EFFORTS)}
        for model_id, label in _CURRENT + _COMPATIBLE
    ]
    claude_cli = [{"id": m, "label": label, "reasoning_efforts": list(_CLAUDE_EFFORTS)}
                  for m, label in _CLAUDE_CLI + _CLAUDE_IDS]
    claude_api = [{"id": m, "label": label} for m, label in _CLAUDE_IDS]
    return {
        "models": {"local_codex": _with_custom("local_codex", entries),
                   "openai": _with_custom("openai", entries),
                   "local_claude": _with_custom("local_claude", claude_cli),
                   "anthropic": _with_custom("anthropic", claude_api)},
        "source": "https://learn.chatgpt.com/docs/models",
        "checked_on": "2026-10-10",
        "availability": "depends_on_account_and_endpoint",
        "custom_models_allowed": True,
    }
