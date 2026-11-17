"""Discover an installed browser on Windows, macOS and Linux."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class BrowserExecutable:
    browser: str
    path: Path


def _name(path: Path) -> str:
    name = str(path).lower()
    if "edge" in name:
        return "Edge"
    if "chromium" in name:
        return "Chromium"
    return "Chrome"


def _registry_paths():
    # Imported only on Windows, so Linux builds do not require winreg.
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for executable in ("msedge.exe", "chrome.exe", "chromium.exe"):
                try:
                    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
                    with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                        value, _ = winreg.QueryValueEx(handle, None)
                    yield Path(os.path.expandvars(str(value).strip('"')))
                except OSError:
                    continue


def discover(override: str | None = None, *, platform: str | None = None,
             env: Mapping[str, str] | None = None) -> BrowserExecutable | None:
    """An invalid override is an error, not a silent fallback to another browser."""
    found = discover_all(override, platform=platform, env=env)
    return found[0] if found else None


def discover_all(override: str | None = None, *, platform: str | None = None,
                 env: Mapping[str, str] | None = None) -> list[BrowserExecutable]:
    """Every installed candidate in preference order; only the override if one is set."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if override:
        path = Path(override).expanduser()
        if not path.is_file() or (not platform.startswith("win") and not os.access(path, os.X_OK)):
            raise ValueError("Browser executable not found or not executable; check browser.path")
        return [BrowserExecutable(_name(path), path.resolve())]
    paths: list[Path] = []
    if platform.startswith("win"):
        if sys.platform.startswith("win"):
            paths.extend(_registry_paths())
        for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = env.get(key)
            if base:
                paths.extend(Path(base) / suffix for suffix in (
                    "Microsoft/Edge/Application/msedge.exe",
                    "Google/Chrome/Application/chrome.exe",
                    "Chromium/Application/chrome.exe",
                ))
    elif platform == "darwin":
        for base in (Path("/Applications"), Path.home() / "Applications"):
            paths.extend(base / suffix for suffix in (
                "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "Google Chrome.app/Contents/MacOS/Google Chrome",
                "Chromium.app/Contents/MacOS/Chromium",
            ))
    else:
        for executable in ("microsoft-edge", "microsoft-edge-stable", "google-chrome",
                           "google-chrome-stable", "chromium", "chromium-browser"):
            path = shutil.which(executable, path=env.get("PATH", ""))
            if path:
                paths.append(Path(path))
    found: list[BrowserExecutable] = []
    for path in paths:
        if path.is_file() and (platform.startswith("win") or os.access(path, os.X_OK)):
            resolved = path.resolve()
            if all(item.path != resolved for item in found):
                found.append(BrowserExecutable(_name(path), resolved))
    return found
