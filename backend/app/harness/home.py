"""GlossX agent files: the global home ``~/.glossx`` and ``<workspace>/.glossx``.

Layout (like Claude Code's ``.claude``)::

    ~/.glossx/                      (override with GLOSSX_HOME)
        GLOSSX.md                   global instructions
        settings.json               harness settings
        commands/*.md  agents/*.md  skills/<name>/SKILL.md

    <workspace>/GLOSSX.md           project instructions (workspace root)
    <workspace>/.glossx/
        settings.json               project overrides (merged over the global ones)
        commands/*.md  agents/*.md
        memory.json                 mirror of the project's memory notes
        checkpoints/                write checkpoints (never committed)

Before ``~/.glossx`` existed these files lived in the data directory. They are
copied here once (``migrate``; the originals are left alone) and the old
locations stay readable as a fallback, so nothing is lost.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .. import config

logger = logging.getLogger(__name__)

CONFIG_DIR = config.WORKSPACE_CONFIG_DIRNAME
LEGACY_SETTINGS_KEY = "glossx_harness"
_lock = threading.RLock()
_migrated = False

HOME_README = """# GlossX home

Your GlossX agent files, shared by every project:

- `GLOSSX.md` - instructions added to every session
- `settings.json` - harness settings (permissions, hooks, context limit, default mode, vcs)
- `commands/*.md` - custom slash commands
- `agents/*.md` - custom subagent types
- `skills/<name>/SKILL.md` - Agent Skills

A project can override these in `<workspace>/.glossx/`.
"""

COMMANDS_README = """# Project commands

Each `<name>.md` here becomes the slash command `/<name>` in this project.
Optional YAML frontmatter: `description`, `argument-hint`, `mode`. The body is
the prompt; `$ARGUMENTS` is replaced by what follows the command.
"""

AGENTS_README = """# Project subagents

Each `<name>.md` here defines a subagent type for the `task` tool in this
project. YAML frontmatter: `name`, `description`, `tools` (comma separated or
`*`), `model`, `max_turns`. The body is the subagent's system prompt.
"""


def home() -> Path:
    return config.GLOSSX_HOME


def workspace_config(root: Path) -> Path:
    return Path(root) / CONFIG_DIR


def paths(workspace_root: Path | None = None) -> dict[str, Any]:
    """Resolved locations (read-only, shown in the settings UI)."""
    out: dict[str, Any] = {
        "home": str(config.GLOSSX_HOME),
        "global_instructions": str(config.GLOBAL_INSTRUCTIONS),
        "settings": str(config.HARNESS_SETTINGS_PATH),
        "commands": str(config.COMMANDS_DIR),
        "agents": str(config.AGENTS_DIR),
        "skills": str(config.SKILLS_DIR),
        "data": str(config.DATA_DIR),
        "workspace_config": None,
        "workspace_instructions": None,
    }
    if workspace_root is not None:
        out["workspace_config"] = str(workspace_config(workspace_root))
        out["workspace_instructions"] = str(Path(workspace_root) / "GLOSSX.md")
    return out


# -- small file helpers -----------------------------------------------------------------
def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def write_if_missing(path: Path, text: str) -> bool:
    """Create ``path`` with ``text`` unless it exists. Never overwrites."""
    try:
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return True
    except OSError:
        logger.warning("could not create %s", path, exc_info=True)
        return False


def _copy_missing(src: Path, dst: Path) -> int:
    """Copy the files of ``src`` that ``dst`` does not have (one level + subdirs)."""
    copied = 0
    try:
        if not src.is_dir():
            return 0
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            target = dst / item.relative_to(src)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
    except OSError:
        logger.warning("could not copy %s to %s", src, dst, exc_info=True)
    return copied


# -- global home ----------------------------------------------------------------------
def migrate(force: bool = False) -> dict[str, Any]:
    """Create ``~/.glossx`` and copy the files that used to live in the data
    directory. Idempotent and non-destructive; never raises."""
    global _migrated
    report: dict[str, Any] = {"home": str(config.GLOSSX_HOME), "copied": []}
    with _lock:
        if _migrated and not force:
            return report
        _migrated = True
        try:
            root = config.GLOSSX_HOME
            for directory in (root, config.COMMANDS_DIR, config.AGENTS_DIR, config.SKILLS_DIR):
                directory.mkdir(parents=True, exist_ok=True)
            write_if_missing(root / "README.md", HOME_README)
            legacy = config.LEGACY_GLOBAL_INSTRUCTIONS
            if legacy.is_file() and not config.GLOBAL_INSTRUCTIONS.exists():
                shutil.copy2(legacy, config.GLOBAL_INSTRUCTIONS)
                report["copied"].append("GLOSSX.md")
            write_if_missing(config.GLOBAL_INSTRUCTIONS, "# Global GlossX instructions\n\nAdd personal instructions for every workspace here.\n")
            for name, src, dst in (("commands", config.LEGACY_COMMANDS_DIR, config.COMMANDS_DIR),
                                   ("agents", config.LEGACY_AGENTS_DIR, config.AGENTS_DIR)):
                if src.resolve() != dst.resolve() and _copy_missing(src, dst):
                    report["copied"].append(name)
            if not config.HARNESS_SETTINGS_PATH.exists():
                raw = config.load_config().get(LEGACY_SETTINGS_KEY)
                if isinstance(raw, dict) and raw:
                    write_json_atomic(config.HARNESS_SETTINGS_PATH, raw)
                    report["copied"].append("settings.json")
                else:
                    write_json_atomic(config.HARNESS_SETTINGS_PATH, {})
        except Exception:  # a read-only home must not stop the server
            logger.exception("could not prepare the GlossX home %s", config.GLOSSX_HOME)
    return report


def global_instruction_files() -> list[Path]:
    """The global ``GLOSSX.md`` (new location first, the old one as a fallback)."""
    out = []
    for path in (config.GLOBAL_INSTRUCTIONS, config.LEGACY_GLOBAL_INSTRUCTIONS):
        try:
            if path.is_file():
                out.append(path)
                break
        except OSError:
            continue
    return out


def global_dirs(kind: str) -> list[Path]:
    """Directories holding the user's ``commands`` / ``agents`` markdown files."""
    new, old = {"commands": (config.COMMANDS_DIR, config.LEGACY_COMMANDS_DIR),
                "agents": (config.AGENTS_DIR, config.LEGACY_AGENTS_DIR)}[kind]
    return [new] if new.resolve() == old.resolve() else [new, old]


# -- settings files ---------------------------------------------------------------------
def read_json(path: Path) -> Any:
    """Parsed JSON file, or ``None`` when it is missing or unreadable."""
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return None


def read_global_settings() -> Any:
    """Raw global harness settings: ``~/.glossx/settings.json``, else the old
    ``config.json`` key."""
    raw = read_json(config.HARNESS_SETTINGS_PATH)
    if isinstance(raw, dict):
        return raw
    return config.load_config().get(LEGACY_SETTINGS_KEY)


def write_global_settings(data: dict[str, Any]) -> None:
    with _lock:
        write_json_atomic(config.HARNESS_SETTINGS_PATH, data)


def read_workspace_settings(root: Path | None) -> dict[str, Any]:
    if root is None:
        return {}
    raw = read_json(workspace_config(root) / "settings.json")
    return raw if isinstance(raw, dict) else {}


# -- per-workspace .glossx --------------------------------------------------------------
def seed_workspace(root: Path) -> None:
    """Create ``<workspace>/.glossx`` (settings.json, commands/, agents/). Only
    missing files are created, so calling it on an old workspace is safe."""
    cfg = workspace_config(root)
    try:
        if (cfg / "settings.json").exists():
            return
        (cfg / "commands").mkdir(parents=True, exist_ok=True)
        (cfg / "agents").mkdir(parents=True, exist_ok=True)
        write_if_missing(cfg / "commands" / "README.md", COMMANDS_README)
        write_if_missing(cfg / "agents" / "README.md", AGENTS_README)
        write_if_missing(cfg / "settings.json", "{}\n")
    except OSError:
        logger.warning("could not seed %s", cfg, exc_info=True)


def write_memory(root: Path, notes: list[dict[str, Any]]) -> None:
    """Mirror a project's memory notes to ``<workspace>/.glossx/memory.json``."""
    try:
        write_json_atomic(workspace_config(root) / "memory.json", {
            "about": "Project memory notes. Maintained by GlossX (edit them in the app); "
                     "this file is a mirror that travels with the workspace.",
            "notes": [
                {"id": n.get("id"), "text": n.get("text"), "ts": n.get("ts"),
                 "source_run": n.get("source_run"), "span_ids": list(n.get("span_ids") or [])}
                for n in notes
            ],
        })
    except OSError:
        logger.warning("could not write the memory mirror in %s", root, exc_info=True)
