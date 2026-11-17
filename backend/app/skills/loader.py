"""Discover + parse Claude / Codex skills (and slash-commands) from disk.

Formats (confirmed on this machine):
  - Claude skills:   ~/.claude/skills/<name>/SKILL.md, <cwd>/.claude/skills/...,
                     plugin skills under ~/.claude/plugins/**/skills/<name>/SKILL.md
  - Codex skills:    $CODEX_HOME/skills/**/<name>/SKILL.md   (default ~/.codex)
  - Slash commands:  **/commands/*.md
All are Markdown with a leading YAML frontmatter block (name/description/…).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

HOME = Path.home()


def _frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` YAML block from the Markdown body."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            raw = text[3:end].strip()
            body = text[end + 4 :].lstrip("\n")
            try:
                meta = yaml.safe_load(raw) or {}
                if isinstance(meta, dict):
                    return meta, body
            except yaml.YAMLError:
                pass
    return {}, text


def _exists(path: Path) -> bool:
    """``Path.exists`` that treats unreadable locations as missing."""
    try:
        return path.exists()
    except OSError:
        return False


def _safe_glob(iterator):
    """Iterate a (r)glob, stopping quietly at unreadable directories."""
    try:
        yield from iterator
    except OSError:
        return


def _skill_search_roots() -> list[tuple[str, Path]]:
    from .. import config
    roots: list[tuple[str, Path]] = [("glossx", config.SKILLS_DIR)]
    # Claude personal + project
    for base in {HOME, Path("/home/czb"), Path("/home/cjc"), Path.cwd()}:
        roots.append(("claude", base / ".claude" / "skills"))
    # Claude plugin skills
    roots.append(("claude-plugin", HOME / ".claude" / "plugins"))
    roots.append(("claude-plugin", Path("/home/czb/.claude/plugins")))
    # Codex
    codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
    roots.append(("codex", codex_home / "skills"))
    roots.append(("codex", Path("/home/czb/.codex/skills")))
    # de-dup while preserving order
    seen = set()
    uniq = []
    for src, p in roots:
        key = str(p.resolve()) if _exists(p) else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((src, p))
    return uniq


def _parse_allowed_tools(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def discover_skills() -> list[dict]:
    """Return skill descriptors (metadata only; body loaded on demand)."""
    found: dict[str, dict] = {}
    for source, root in _skill_search_roots():
        if not _exists(root):
            continue
        for skill_md in _safe_glob(root.rglob("SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            meta, _ = _frontmatter(text)
            name = (meta.get("name") or skill_md.parent.name).strip()
            if not name:
                continue
            desc = meta.get("description", "")
            if isinstance(desc, str):
                desc = " ".join(desc.split())
            key = f"{source}:{name}"
            found[key] = {
                "id": key,
                "name": name,
                "source": source,
                "type": "skill",
                "description": desc[:400],
                "argument_hint": meta.get("argument-hint", ""),
                "allowed_tools": _parse_allowed_tools(meta.get("allowed-tools")),
                "path": str(skill_md),
            }
    # slash-commands (personal + plugin)
    for source, root in [
        ("claude", HOME / ".claude" / "commands"),
        ("claude", Path("/home/czb/.claude/commands")),
        ("claude-plugin", HOME / ".claude" / "plugins"),
    ]:
        if not _exists(root):
            continue
        globber = root.rglob("commands/*.md") if root.name == "plugins" else root.glob("*.md")
        for cmd_md in _safe_glob(globber):
            try:
                text = cmd_md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            meta, _ = _frontmatter(text)
            name = cmd_md.stem
            key = f"{source}:cmd:{name}"
            found.setdefault(key, {
                "id": key,
                "name": name,
                "source": source,
                "type": "command",
                "description": " ".join(str(meta.get("description", "")).split())[:400],
                "argument_hint": meta.get("argument-hint", ""),
                "allowed_tools": _parse_allowed_tools(meta.get("allowed-tools")),
                "path": str(cmd_md),
            })
    return sorted(found.values(), key=lambda s: (s["source"], s["name"]))


def load_skill_body(path: str) -> tuple[dict, str]:
    """Return (frontmatter, body) for a skill/command file."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return _frontmatter(text)
