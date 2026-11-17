"""Settings for pairing the GlossX mobile app with this computer."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import remote_access, tunnel

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _local_only(request: Request) -> None:
    if not remote_access.is_local_request(request.client.host if request.client else None, request.headers):
        raise HTTPException(403, "Only this computer can change mobile access")


def _status(request: Request, pair: bool = False) -> dict:
    state = remote_access.load_state()
    server = request.scope.get("server") or (None, None)
    bound = os.environ.get("GLOSS_BIND_HOST", "")
    listening_port = server[1] or state["port"]
    # bound on all interfaces (npm start, or the desktop app with mobile access on)
    reachable = bound in {"0.0.0.0", "::"}
    port = listening_port if reachable else state["port"]
    addresses = remote_access.lan_addresses()
    code = remote_access.new_pair_code() if pair else None
    tunnel.start_if_enabled(listening_port)
    via = tunnel.TUNNEL.status()
    return {
        "enabled": state["enabled"],
        "reachable": reachable,
        "restart_required": state["enabled"] and not reachable,
        "auth": remote_access.auth_enabled(),
        "port": port,
        "token": remote_access.token(),
        "addresses": addresses,
        "urls": [f"http://{ip}:{port}" for ip in addresses],
        # what the QR holds: open it on a phone and that device is signed in
        "pair_urls": [f"http://{ip}:{port}/pair?c={code}" for ip in addresses] if code else [],
        # over the internet, through the Cloudflare tunnel
        "tunnel": {**via, "enabled": bool(state.get("tunnel"))},
        "tunnel_pair_url": f"{via['url']}/pair?c={code}" if code and via["url"] else None,
        "pair_expires_in": remote_access.PAIR_TTL,
        "devices": remote_access.devices(),
    }


@router.get("")
async def get_remote(request: Request, pair: bool = False):
    """``?pair=1`` also mints a new QR code (replacing the previous one)."""
    _local_only(request)
    return _status(request, pair)


class RemotePatch(BaseModel):
    enabled: bool | None = None
    port: int | None = Field(default=None, ge=1024, le=65535)
    rotate_token: bool = False
    remove_device: str | None = None
    remove_all_devices: bool = False
    tunnel: bool | None = None


@router.post("")
async def update_remote(body: RemotePatch, request: Request):
    _local_only(request)
    remote_access.update(enabled=body.enabled, port=body.port, rotate=body.rotate_token, tunnel=body.tunnel)
    if body.tunnel is False:
        await tunnel.TUNNEL.stop()
    elif body.tunnel:
        tunnel.TUNNEL.error = ""  # an explicit switch-on retries after a failure
    if body.remove_device:
        remote_access.remove_device(body.remove_device)
    if body.remove_all_devices:
        remote_access.remove_device(None)
    return _status(request)
