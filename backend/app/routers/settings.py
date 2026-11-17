"""Settings: view/update provider config (secrets masked on read)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..providers import registry
from ..model_catalog import suggestions

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/models")
async def get_model_suggestions():
    """Current suggestions; choosing a model does not grant account access."""
    return suggestions()


@router.get("/local-cli")
async def get_local_cli(refresh: bool = False):
    from ..cli_discovery import snapshot
    return {"tools": await asyncio.to_thread(snapshot, refresh), "claude_executed": False}


@router.get("/quota")
async def get_quota(provider: str | None = None, fresh: bool = False):
    """5-hour / weekly usage limits of the accounts Claude Code and Codex are
    signed in to (``?provider=local_claude|local_codex``; both when omitted)."""
    from ..providers import quota

    names = [provider] if provider else ["local_claude", "local_codex"]
    if any(name not in ("local_claude", "local_codex") for name in names):
        raise HTTPException(400, "Usage limits are only available for local_claude and local_codex")
    return {"quotas": [await quota.quota(name, fresh=fresh) for name in names]}


class SettingsPatch(BaseModel):
    paper_workspace_root: str | None = None
    confirm_exit: bool | None = None
    provider: str | None = None
    output_language: str | None = None
    target_language: str | None = None
    providers: dict | None = None
    feature_settings: dict | None = None


@router.get("")
async def get_settings():
    return {
        "config": config.public_config(),
        # available = switched on; all = every provider, for the on/off switches
        "available_providers": registry.available(),
        "all_providers": registry.all_providers(),
        "provider_statuses": registry.provider_statuses(),
    }


@router.get("/status")
async def get_provider_statuses():
    return {"provider_statuses": registry.provider_statuses()}


@router.post("")
async def update_settings(body: SettingsPatch):
    patch: dict = {}
    if body.paper_workspace_root is not None:
        from ..harness.locations import normalize_path

        try:
            patch["paper_workspace_root"] = (str(normalize_path(body.paper_workspace_root))
                                             if body.paper_workspace_root.strip() else "")
            if patch["paper_workspace_root"]:
                from pathlib import Path
                from ..agent import projects

                base = Path(patch["paper_workspace_root"])
                if any(base == root or root in base.parents for _, root in projects.workspace_paths()):
                    raise ValueError("The default location cannot be inside an existing workspace")
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    if body.confirm_exit is not None:
        patch["confirm_exit"] = body.confirm_exit
    if body.provider:
        patch["provider"] = body.provider
    if body.output_language:
        patch["output_language"] = body.output_language
    if body.target_language:
        patch["target_language"] = body.target_language
    if body.providers:
        # ignore masked api_key sentinel so we don't overwrite real keys with "set"
        cleaned = {}
        for name, p in body.providers.items():
            pc = dict(p)
            if pc.get("api_key") in ("set", ""):
                pc.pop("api_key", None)
            cleaned[name] = pc
        patch["providers"] = cleaned
        for name in cleaned:
            registry.reset_status(name)
    if body.feature_settings is not None:
        patch["feature_settings"] = body.feature_settings
    _keep_an_enabled_active_provider(patch)
    config.save_config(patch)
    return {"config": config.public_config()}


def _keep_an_enabled_active_provider(patch: dict) -> None:
    """Apply provider on/off switches safely.

    At least one provider must stay on, and the active provider is moved to the
    first enabled one when the user switches the active provider off.
    """
    current = config.load_config()
    switches = {
        name: (current.get("providers", {}).get(name) or {}).get("enabled", True) is not False
        for name in registry.all_providers()
    }
    for name, values in (patch.get("providers") or {}).items():
        if name in switches and isinstance(values, dict) and "enabled" in values:
            values["enabled"] = bool(values["enabled"])
            switches[name] = values["enabled"]
    enabled = [name for name, on in switches.items() if on]
    if not enabled:
        raise HTTPException(status_code=400, detail="At least one model provider must stay on.")
    active = patch.get("provider") or current.get("provider")
    if active not in enabled:
        patch["provider"] = enabled[0]


class TestBody(BaseModel):
    provider: str | None = None


@router.post("/test")
async def test_provider(body: TestBody):
    """Check connectivity; local CLIs use their fast login-status command."""
    provider = body.provider or config.load_config().get("provider")
    if provider in registry.all_providers() and not registry.is_enabled(provider):
        return {"ok": False, "provider": provider, "disabled": True,
                "error": f"The '{provider}' model provider is turned off in Settings."}
    return await registry.test_connection(provider)
