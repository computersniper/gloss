"""Configuration and platform-appropriate on-disk paths for GlossX."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

from .platform_support import default_cache_dir, glossx_home, read_utf8_text, resolve_data_dir

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent          # backend/
_LEGACY_DATA_DIR = _BACKEND_DIR / "data"
DATA_DIR, USING_LEGACY_DATA_DIR = resolve_data_dir(
    sys.platform,
    os.environ,
    Path.home(),
    _LEGACY_DATA_DIR,
)
CACHE_DIR = Path(
    os.environ.get("GLOSS_CACHE_DIR", default_cache_dir())
).expanduser().resolve()
PAPERS_DIR = DATA_DIR / "papers"
UPLOADS_DIR = DATA_DIR / "uploads"
PLUGINS_DIR = DATA_DIR / "plugins"
PLUGIN_OUTPUTS_DIR = DATA_DIR / "plugin_outputs"
DB_PATH = DATA_DIR / "moonlight.db"
CONFIG_PATH = DATA_DIR / "config.json"
# GlossX agent state: project memory and per-run sandbox workspaces for delegates.
PROJECTS_DIR = DATA_DIR / "projects"
WORKSPACES_DIR = DATA_DIR / "workspaces"
# GlossX harness: agent sessions (library data stays in the data directory).
SESSIONS_DIR = DATA_DIR / "sessions"
# The user's agent files live in ``~/.glossx`` (``GLOSSX_HOME`` overrides), like
# Claude Code's ``~/.claude``: GLOSSX.md, settings.json, commands/, agents/, skills/.
GLOSSX_HOME = glossx_home()
GLOBAL_INSTRUCTIONS = GLOSSX_HOME / "GLOSSX.md"
HARNESS_SETTINGS_PATH = GLOSSX_HOME / "settings.json"
COMMANDS_DIR = GLOSSX_HOME / "commands"
AGENTS_DIR = GLOSSX_HOME / "agents"
SKILLS_DIR = GLOSSX_HOME / "skills"
# Per-workspace agent files: ``<workspace>/.glossx/`` (settings.json, commands/,
# agents/, memory.json, checkpoints/).
WORKSPACE_CONFIG_DIRNAME = ".glossx"
# Where those files lived before ``~/.glossx`` existed. Still read as a fallback
# and copied (never moved) by ``app.harness.home.migrate``.
LEGACY_GLOBAL_INSTRUCTIONS = DATA_DIR / "GLOSSX.md"
LEGACY_COMMANDS_DIR = DATA_DIR / "commands"
LEGACY_AGENTS_DIR = DATA_DIR / "agents"
LEGACY_CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
CHECKPOINTS_DIR = LEGACY_CHECKPOINTS_DIR  # old name, kept for external scripts

APP_NAME = "Gloss"
APP_VERSION = "2.0.0-beta.1"

for _d in (DATA_DIR, CACHE_DIR, PAPERS_DIR, UPLOADS_DIR, PLUGINS_DIR, PLUGIN_OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Directory served as the built frontend (populated by `vite build`). Frozen
# desktop builds point this at the copy embedded by PyInstaller.
FRONTEND_DIST = Path(
    os.environ.get(
        "GLOSS_FRONTEND_DIST",
        str(_BACKEND_DIR.parent / "frontend" / "dist"),
    )
).expanduser().resolve()

# ---------------------------------------------------------------------------
# Isolated HOME for the `claude` CLI subprocess.
#
# The CLI otherwise auto-discovers the user's ~/.claude/CLAUDE.md + memory +
# project context, which leaks into paper answers. We run it under a sandbox
# HOME that symlinks ONLY the auth files (credentials + config), so it keeps the
# subscription (OAuth) but sees no CLAUDE.md / memory / settings.
# ---------------------------------------------------------------------------
CLAUDE_SANDBOX_HOME = (CACHE_DIR / "claude-home").resolve()


def ensure_claude_sandbox() -> Path:
    """Create/refresh the sandbox HOME for the claude CLI. Idempotent."""
    import os as _os
    from pathlib import Path as _Path

    real_home = _Path(_os.path.expanduser("~"))
    sb = CLAUDE_SANDBOX_HOME
    (sb / ".claude").mkdir(parents=True, exist_ok=True)

    def _link_or_copy(src: Path, dst: Path) -> None:
        if not src.exists():
            return
        if dst.is_symlink():
            return
        if dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
            return
        try:
            if not dst.exists():
                dst.symlink_to(src)
        except OSError:
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass

    _link_or_copy(real_home / ".claude" / ".credentials.json", sb / ".claude" / ".credentials.json")
    _link_or_copy(real_home / ".claude.json", sb / ".claude.json")
    return sb


# Clean working dir for the `codex exec` subprocess (no AGENTS.md to leak).
CODEX_SANDBOX = (CACHE_DIR / "codex-sandbox").resolve()


def ensure_codex_sandbox() -> Path:
    CODEX_SANDBOX.mkdir(parents=True, exist_ok=True)
    return CODEX_SANDBOX

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST = os.environ.get("GLOSS_HOST", "0.0.0.0")
PORT = int(os.environ.get("GLOSS_PORT", "8010"))

# ---------------------------------------------------------------------------
# Provider / app configuration (persisted)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    # Empty preserves the built-in location; changes apply to new paper projects.
    "paper_workspace_root": "",
    # desktop app: warn before closing because in-flight work is interrupted
    "confirm_exit": True,
    # active LLM provider: "local_claude" | "anthropic" | "openai"
    "provider": "local_claude",
    # language the AI ANSWERS in — summaries, explanations, chat, highlight notes
    "output_language": "中文 (Simplified Chinese)",
    # default TARGET language for the translate feature
    "target_language": "中文 (Simplified Chinese)",
    # Stable feature identifiers map to optional host and plugin overrides.
    # Missing keys inherit the active provider's defaults.
    "feature_settings": {},
    "providers": {
        "local_claude": {
            "cli_path": "",  # blank auto-detects local installations
            # short names the claude CLI understands, or full claude model ids
            "model": "sonnet",
            # reasoning effort: "" (CLI default) | low | medium | high | xhigh | max
            "effort": "",
            "timeout": 600,
        },
        "local_codex": {
            "cli_path": "",  # blank auto-detects; explicit executable/npm shim wins
            # empty model = use codex's own configured default
            "model": "",
            # codex reasoning effort: "" | minimal | low | medium | high
            "effort": "medium",
            "timeout": 600,
        },
        "anthropic": {
            "model": "claude-sonnet-4-5",
            "api_key": "",
            "base_url": "https://api.anthropic.com",
            "timeout": 300,
        },
        "openai": {
            # Works against real OpenAI, or any OpenAI-compatible endpoint,
            # e.g. the local claude_proxy at http://127.0.0.1:8899/v1
            "model": "gpt-4o-mini",
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "timeout": 300,
        },
    },
}

_lock = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    """Return the persisted config merged over defaults (so new keys appear)."""
    if CONFIG_PATH.exists():
        try:
            user = json.loads(read_utf8_text(CONFIG_PATH))
        except (json.JSONDecodeError, OSError, UnicodeError):
            user = {}
    else:
        user = {}
    return _deep_merge(DEFAULT_CONFIG, user)


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Persist config (merged over the current one) and return the result."""
    with _lock:
        merged = _deep_merge(load_config(), cfg)
        CONFIG_PATH.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return merged


def output_language() -> str:
    """Language the AI should answer in (summaries / explain / chat)."""
    return load_config().get("output_language", "中文 (Simplified Chinese)")


def resolve_feature_settings(
    feature_id: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve inherited provider and plugin settings for one feature."""
    cfg = load_config()
    feature_settings = cfg.get("feature_settings", {})
    feature_settings = feature_settings if isinstance(feature_settings, dict) else {}
    saved = feature_settings.get(feature_id)
    if saved is None and feature_id.startswith("plugin:"):
        # Plugin settings use the manifest ID as their durable namespace.
        saved = feature_settings.get(feature_id.removeprefix("plugin:"))
    saved = saved or {}
    saved = saved if isinstance(saved, dict) else {}
    runtime = overrides if isinstance(overrides, dict) else {}

    provider = runtime.get("provider")
    if provider is None:
        provider = saved.get("provider")
    provider = provider or cfg.get("provider", "local_claude")
    provider_cfg = cfg.get("providers", {}).get(provider, {})

    def inherited(name: str, default: Any = None) -> Any:
        value = runtime.get(name)
        if value not in (None, ""):
            return value
        value = saved.get(name)
        if value not in (None, ""):
            return value
        return provider_cfg.get(name, default)

    context_mode = inherited("context_mode", "full")
    if context_mode not in {"full", "shared_session"}:
        context_mode = "full"
    configuration = saved.get("configuration", {})
    configuration = dict(configuration) if isinstance(configuration, dict) else {}
    runtime_configuration = runtime.get("configuration")
    if isinstance(runtime_configuration, dict):
        configuration = _deep_merge(configuration, runtime_configuration)

    return {
        "provider": provider,
        "model": inherited("model", ""),
        "effort": inherited("effort", ""),
        "context_mode": context_mode,
        "configuration": configuration,
    }


def public_config() -> dict[str, Any]:
    """Config with secrets masked, safe to send to the browser."""
    cfg = load_config()
    out = json.loads(json.dumps(cfg))  # deep copy
    for name, p in out.get("providers", {}).items():
        if p.get("api_key"):
            p["api_key"] = "set"
        else:
            p["api_key"] = ""
    return out
