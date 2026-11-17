"""Local CLI discovery. No login, model call or Claude execution during detection."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .platform_support import hidden_subprocess_options, resolve_cli_command

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
_PACKAGES = {"codex": "@openai/codex", "claude": "@anthropic-ai/claude-code", "gemini": "@google/gemini-cli", "opencode": "opencode-ai"}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0  # Desktop updates can replace files while discovery is scanning.


def candidate_paths(name: str) -> list[tuple[Path, str]]:
    candidates = []
    seen = set()
    def add(path: Path, source: str):
        if path.is_file():
            key = os.path.normcase(str(path.resolve()))
            if key not in seen:
                seen.add(key)
                candidates.append((path.resolve(), source))
    suffixes = (".exe", ".cmd", ".ps1") if sys.platform == "win32" else ("",)
    # PATH alone can select an old npm install before a desktop-bundled CLI.
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory.strip():
            for suffix in suffixes:
                add(Path(directory.strip().strip('"')) / (name + suffix), "PATH")
    home = Path.home()
    for folder in (home / ".local/bin", home / ".codex/bin", home / ".claude/local", home / "bin"):
        for suffix in suffixes:
            add(folder / (name + suffix), "user installation")
    if name == "codex":
        if sys.platform == "win32":
            root = Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local") / "OpenAI/Codex/bin"
            if root.is_dir():
                for executable in sorted(root.glob("*/codex.exe"), key=_mtime, reverse=True)[:12]:
                    add(executable, "Codex desktop")
        elif sys.platform == "darwin":
            for app in (Path("/Applications/Codex.app"), home / "Applications/Codex.app"):
                add(app / "Contents/Resources/codex", "Codex desktop")
    # Windows npm installations may not be present in a desktop's inherited PATH.
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or home / "AppData/Roaming") / "npm"
        for suffix in suffixes:
            add(root / (name + suffix), "npm")
    return candidates


def _package_version(path: Path, name: str) -> str | None:
    for package in (path.parent / "node_modules" / _PACKAGES[name] / "package.json", path.parent / "package.json"):
        try:
            value = json.loads(package.read_text(encoding="utf-8"))
            if value.get("name") == _PACKAGES[name]:
                return str(value.get("version") or "") or None
        except (OSError, ValueError):
            pass
    return None


def _version_key(version: str | None) -> tuple:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)(.*)", version or "")
    if not match:
        return (0, 0, 0, False)
    return (*map(int, match.groups()[:3]), not bool(match.group(4)))


def discover(name: str, *, refresh: bool = False) -> list[dict]:
    if name not in _PACKAGES:
        raise ValueError("Unsupported local CLI")
    with _lock:
        cached = _cache.get(name)
        if not refresh and cached and time.monotonic() - cached[0] < 300:
            return [dict(item) for item in cached[1]]
        results = []
        commands = set()
        for path, source in candidate_paths(name):
            try:
                command = resolve_cli_command(str(path))
            except (OSError, RuntimeError):
                continue  # An npm shim without Node cannot be invoked.
            key = tuple(os.path.normcase(part) for part in command)
            if key in commands:
                continue
            commands.add(key)
            version = _package_version(path, name)
            # Only Codex gets a version-only probe; NEVER execute Claude to detect it.
            checked = False
            if name == "codex":
                try:
                    result = subprocess.run(command + ["--version"], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                        timeout=3, **hidden_subprocess_options())
                    match = re.search(r"codex-cli\s+(\d+\.\d+\.\d+[^\s]*)", result.stdout)
                    if result.returncode == 0 and match:
                        version = match.group(1)
                        checked = True
                except (OSError, subprocess.SubprocessError):
                    pass
            results.append({"path": str(path), "command": command, "version": version,
                            "source": source, "version_checked": checked})
        # Highest known Codex version fixes old PATH installs shadowing the desktop CLI.
        if name == "codex":
            results.sort(key=lambda item: _version_key(item["version"]), reverse=True)
        _cache[name] = (time.monotonic(), results)
        return [dict(item) for item in results]


def select_command(name: str, explicit: str = "") -> list[str]:
    if explicit.strip():
        return resolve_cli_command(explicit.strip())
    found = discover(name)
    return list(found[0]["command"]) if found else resolve_cli_command(name)


def snapshot(refresh: bool = False) -> dict:
    return {name: discover(name, refresh=refresh) for name in _PACKAGES}
