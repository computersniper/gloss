"""Local environment and installation diagnostics for the in-app Doctor."""
from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from .. import config
from ..library import store
from ..plugins import manager as plugin_manager
from ..providers import registry

router = APIRouter(prefix="/api/doctor", tags=["doctor"])


def _check(check_id: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "status": status, "detail": detail}


def _writable_probe(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".gloss-doctor-", dir=path, delete=True
        ) as probe:
            probe.write(b"ok")
            probe.flush()
        return True, str(path)
    except OSError as error:
        return False, f"{path}: {error}"


def _database_check() -> tuple[str, str, int]:
    if not config.DB_PATH.is_file():
        return "fail", f"Database file is missing: {config.DB_PATH}", 0
    try:
        with sqlite3.connect(config.DB_PATH, timeout=5) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        paper_count = len(store.list_papers())
        if not result or result[0] != "ok":
            return "fail", f"SQLite quick_check returned: {result}", paper_count
        return "pass", f"SQLite quick_check: ok; {paper_count} papers", paper_count
    except sqlite3.Error as error:
        return "fail", f"SQLite check failed: {error}", 0


def _provider_checks(settings: dict[str, Any]) -> list[dict[str, str]]:
    active = str(settings.get("provider") or "local_claude")
    available = registry.available()
    checks: list[dict[str, str]] = []
    if active not in available:
        checks.append(_check("provider", "fail", f"Unknown active provider: {active}"))
        return checks

    state = registry.provider_statuses().get(active, {})
    if state.get("status") == "connected":
        provider_status = "pass"
        detail = f"{active} connected"
    elif state.get("status") == "error":
        provider_status = "fail"
        detail = f"{active}: {state.get('error') or 'connection test failed'}"
    else:
        provider_status = "warning"
        detail = f"{active} has not been connection-tested in this session"
    checks.append(_check("provider", provider_status, detail))

    if active in {"local_claude", "local_codex"}:
        executable = "claude" if active == "local_claude" else "codex"
        resolved = shutil.which(executable)
        checks.append(
            _check(
                "provider_runtime",
                "pass" if resolved else "fail",
                resolved or f"{executable} CLI was not found on PATH",
            )
        )
    else:
        provider_settings = (settings.get("providers") or {}).get(active) or {}
        has_key = bool(provider_settings.get("api_key"))
        base_url = str(provider_settings.get("base_url") or "").strip()
        checks.append(
            _check(
                "provider_runtime",
                "pass" if has_key and base_url else "fail",
                (
                    f"API credentials configured for {base_url}"
                    if has_key and base_url
                    else "API key or base URL is missing"
                ),
            )
        )
    return checks


@router.post("")
async def run_doctor(request: Request):
    """Run bounded local checks. This endpoint never sends a model request."""
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "runtime",
            "pass",
            f"Gloss {request.app.version}; Python {platform.python_version()}",
        )
    )

    index = config.FRONTEND_DIST / "index.html"
    assets = config.FRONTEND_DIST / "assets"
    frontend_ready = index.is_file() and assets.is_dir() and any(assets.iterdir())
    checks.append(
        _check(
            "frontend",
            "pass" if frontend_ready else "fail",
            str(config.FRONTEND_DIST)
            if frontend_ready
            else f"Built frontend is incomplete: {config.FRONTEND_DIST}",
        )
    )

    data_ok, data_detail = _writable_probe(config.DATA_DIR)
    cache_ok, cache_detail = _writable_probe(config.CACHE_DIR)
    checks.append(_check("data_dir", "pass" if data_ok else "fail", data_detail))
    checks.append(_check("cache_dir", "pass" if cache_ok else "fail", cache_detail))

    database_status, database_detail, paper_count = _database_check()
    checks.append(_check("database", database_status, database_detail))

    try:
        disk = shutil.disk_usage(config.DATA_DIR)
        free_gb = disk.free / (1024 ** 3)
        disk_status = "warning" if free_gb < 1 else "pass"
        checks.append(_check("disk", disk_status, f"{free_gb:.1f} GB free"))
    except OSError as error:
        checks.append(_check("disk", "warning", f"Disk usage unavailable: {error}"))

    settings = config.load_config()
    checks.extend(_provider_checks(settings))
    installed_plugins = plugin_manager.list_installed()
    checks.append(
        _check("plugins", "pass", f"{len(installed_plugins)} installed plugins validated")
    )

    failed = sum(item["status"] == "fail" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    overall = "fail" if failed else "warning" if warnings else "pass"
    packaged = bool(getattr(sys, "frozen", False))

    return {
        "status": overall,
        "checked_at": time.time(),
        "version": request.app.version,
        "summary": {
            "passed": sum(item["status"] == "pass" for item in checks),
            "warnings": warnings,
            "failed": failed,
        },
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid": os.getpid(),
            "packaged": packaged,
            "data_dir": str(config.DATA_DIR),
            "cache_dir": str(config.CACHE_DIR),
            "frontend_dist": str(config.FRONTEND_DIST),
            "active_provider": settings.get("provider"),
            "paper_count": paper_count,
            "plugin_count": len(installed_plugins),
        },
        "checks": checks,
    }
