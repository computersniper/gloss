"""Access from other devices (the GlossX mobile app) to this computer's backend.

The phone and tablet apps do not run Claude Code or Codex themselves: they open
this backend over the local network and drive the CLIs installed here.

Requests from this computer (loopback) are always allowed, so the desktop app
and ``npm start`` in a local browser are unchanged. A request from any other
address must carry the pairing code, as

* ``?token=<code>`` on a page load (the mobile app's first navigation), which
  also sets an HttpOnly cookie so the SPA's relative ``/api`` calls, PDF loads
  and EventSource streams carry it from then on;
* the ``gloss_token`` cookie;
* an ``X-Gloss-Token`` or ``Authorization: Bearer`` header.

Phones and tablets without the app pair by scanning a QR code: it holds
``/pair?c=<one-time code>`` (valid for 10 minutes, used once), which signs that
device in with its own token, kept as the ``gloss_device`` cookie and in the
landing URL (``/?d=<token>``) so "Add to Home Screen" on iOS, whose home-screen
apps keep their own cookies, stays signed in. Each device can be signed out on
its own; a new pairing code signs out all of them.

``GLOSS_REMOTE_AUTH=off`` turns the check off (a trusted network behind your own
auth proxy). ``GLOSS_ACCESS_TOKEN`` pins the code instead of the generated one.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs

from . import config

STATE_PATH = config.DATA_DIR / "remote_access.json"
DEFAULT_PORT = 8010
COOKIE = "gloss_token"
DEVICE_COOKIE = "gloss_device"
PAIR_TTL = 600  # seconds a QR code stays valid
MAX_DEVICES = 8
# reachable without signing in: the pairing link and what "Add to Home Screen" fetches
PUBLIC_PATHS = {"/pair", "/manifest.webmanifest", "/favicon.svg", "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"}
# no 0/o/1/l/i: the code is read off one screen and typed on another
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_lock = threading.Lock()


def _new_token() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _normalize(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    state.setdefault("enabled", False)
    state.setdefault("port", DEFAULT_PORT)
    return state


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def token() -> str:
    """The pairing code; generated on first use and kept across restarts."""
    pinned = os.environ.get("GLOSS_ACCESS_TOKEN", "").strip()
    if pinned:
        return pinned
    with _lock:
        state = load_state()
        if not state.get("token"):
            state["token"] = _new_token()
            _write_state(state)
        return state["token"]


def update(enabled: bool | None = None, port: int | None = None, rotate: bool = False,
           tunnel: bool | None = None) -> dict[str, Any]:
    with _lock:
        state = load_state()
        if enabled is not None:
            state["enabled"] = bool(enabled)
        if tunnel is not None:
            state["tunnel"] = bool(tunnel)
        if port is not None:
            state["port"] = int(port)
        if rotate or not state.get("token"):
            state["token"] = _new_token()
            state["devices"] = []  # a new code signs every device out
        _write_state(state)
        return state


def auth_enabled() -> bool:
    return os.environ.get("GLOSS_REMOTE_AUTH", "").strip().lower() not in {"off", "0", "false", "no"}


def is_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "testclient":  # Starlette's TestClient
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


# set by a reverse proxy in front of us, such as the cloudflared tunnel
PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded")


def is_local_request(host: str | None, headers) -> bool:
    """From this computer itself: loopback, and not relayed by a tunnel."""
    return is_local(host) and not any(headers.get(h) for h in PROXY_HEADERS)


# brute-force guard for the internet-facing tunnel: too many wrong codes pause sign-in
_failures: list[float] = []
FAIL_LIMIT, FAIL_WINDOW = 30, 60.0


def _locked_out() -> bool:
    now = time.time()
    _failures[:] = [t for t in _failures if now - t < FAIL_WINDOW]
    return len(_failures) >= FAIL_LIMIT


def valid(code: str | None) -> bool:
    if not code:
        return False
    return hmac.compare_digest(_normalize(code).encode(), _normalize(token()).encode())


# ---- per-device sign-in (QR pairing) -------------------------------------------

_pair_code: tuple[str, float] | None = None  # the one live QR code and when it expires


def new_pair_code() -> str:
    """A fresh one-time code for the QR; it replaces the previous one."""
    global _pair_code
    code = secrets.token_urlsafe(18)
    _pair_code = (code, time.time() + PAIR_TTL)
    return code


def _take_pair_code(code: str | None) -> bool:
    global _pair_code
    live = _pair_code
    if not code or not live or time.time() > live[1]:
        return False
    if not hmac.compare_digest(code.encode(), live[0].encode()):
        return False
    _pair_code = None  # single use
    return True


def _hash(device_token: str) -> str:
    return hashlib.sha256(device_token.encode()).hexdigest()


def _device_name(user_agent: str) -> str:
    for needle, name in (("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android"), ("Macintosh", "Mac"), ("Windows", "Windows")):
        if needle in user_agent:
            return name
    return "Device"


def add_device(user_agent: str) -> str:
    """Sign a device in; returns its token (only its hash is stored)."""
    device_token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        state = load_state()
        devices = [d for d in state.get("devices", []) if isinstance(d, dict)]
        devices.append({"id": secrets.token_hex(6), "hash": _hash(device_token), "name": _device_name(user_agent),
                        "created": now, "last_seen": now})
        state["devices"] = devices[-MAX_DEVICES:]  # the oldest goes first
        _write_state(state)
    return device_token


def device_for(device_token: str | None) -> dict[str, Any] | None:
    if not device_token:
        return None
    digest = _hash(device_token)
    for device in load_state().get("devices", []):
        if isinstance(device, dict) and hmac.compare_digest(str(device.get("hash", "")), digest):
            return device
    return None


def _touch(device_id: str) -> None:
    with _lock:
        state = load_state()
        for device in state.get("devices", []):
            if device.get("id") == device_id:
                device["last_seen"] = time.time()
        _write_state(state)


def devices() -> list[dict[str, Any]]:
    return [{k: d[k] for k in ("id", "name", "created", "last_seen") if k in d}
            for d in load_state().get("devices", []) if isinstance(d, dict)]


def remove_device(device_id: str | None = None) -> None:
    """Sign out one device, or every device when no id is given."""
    with _lock:
        state = load_state()
        state["devices"] = [d for d in state.get("devices", []) if device_id and d.get("id") != device_id]
        _write_state(state)


def lan_addresses() -> list[str]:
    """IPv4 addresses other devices on the network can try, best first."""
    found: list[str] = []
    try:
        # no packet is sent; this only asks the OS which interface routes out
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out: list[str] = []
    for ip in found:
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or ip in out:
            continue
        out.append(ip)
    return out


def launch_target(host: str, port: int, host_explicit: bool) -> tuple[str, int]:
    """Where the desktop backend should listen: on the network at a stable port
    once "Allow mobile devices" is on, otherwise exactly as requested."""
    if host_explicit or not load_state().get("enabled"):
        return host, port
    return "0.0.0.0", int(load_state().get("port") or DEFAULT_PORT)


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _page(status: int, title: str, text: str) -> tuple[int, bytes]:
    body = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>GlossX</title><body style="font:17px system-ui,sans-serif;padding:32px;max-width:520px;margin:auto">'
        f"<h2>{html.escape(title)}</h2><p>{html.escape(text)}</p></body>"
    )
    return status, body.encode()


async def _respond(send, status: int, body: bytes, content_type: bytes, extra: list[tuple[bytes, bytes]] = ()) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", content_type), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store"), *extra,
    ]})
    await send({"type": "http.response.body", "body": body})


def _device_cookie(device_token: str) -> tuple[bytes, bytes]:
    return (b"set-cookie", f"{DEVICE_COOKIE}={device_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000".encode())


class RemoteAuthMiddleware:
    """Pure ASGI so streaming responses and WebSockets pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or not auth_enabled():
            return await self.app(scope, receive, send)
        client = scope.get("client")
        headers = _headers(scope)
        if is_local_request(client[0] if client else None, headers):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))

        if scope["type"] == "http" and path == "/pair":
            return await self._pair(scope, send, headers, (query.get("c") or [None])[0])
        if scope["type"] == "http" and path in PUBLIC_PATHS:
            return await self.app(scope, receive, send)

        from_query = (query.get("token") or [None])[0]
        device_query = (query.get("d") or [None])[0]
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            pass
        bearer = headers.get("authorization", "")
        candidates = [
            headers.get("x-gloss-token"),
            bearer[7:] if bearer.lower().startswith("bearer ") else None,
            cookie[COOKIE].value if COOKIE in cookie else None,
            from_query,
        ]
        device = device_for(cookie[DEVICE_COOKIE].value if DEVICE_COOKIE in cookie else None) or device_for(device_query)
        if not device and _locked_out():
            return await _respond(send, 429, b'{"detail":"GlossX: too many wrong codes, try again in a minute"}', b"application/json")
        if not device and not any(valid(c) for c in candidates):
            if any(candidates) or device_query:
                _failures.append(time.time())
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
                return
            if scope["type"] == "http" and not path.startswith("/api/"):
                status, body = _page(401, "GlossX", "这台设备还没有配对，或已在电脑上被移除。请在电脑的 GlossX → 设置 → 移动端访问 里扫码重新配对。"
                                     " / This device isn't paired: scan the QR code in GlossX → Settings → Mobile access on the computer.")
                return await _respond(send, status, body, b"text/html; charset=utf-8")
            body = b'{"detail":"GlossX: pairing code required for devices other than this computer"}'
            return await _respond(send, 401, body, b"application/json")

        if device and time.time() - float(device.get("last_seen", 0)) > 60:
            _touch(device["id"])

        extra: list[tuple[bytes, bytes]] = []
        if scope["type"] == "http" and valid(from_query):
            # remember the code so the page's own requests don't need it
            extra.append((b"set-cookie", f"{COOKIE}={_normalize(token())}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000".encode()))
        if scope["type"] == "http" and device_query and device_for(device_query):
            extra.append(_device_cookie(device_query))
        if not extra:
            return await self.app(scope, receive, send)

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _pair(self, scope, send, headers, code):
        if scope.get("method") != "GET" or _locked_out() or not _take_pair_code(code):
            _failures.append(time.time())
            status, body = _page(410, "二维码已失效 / QR code expired",
                                 "请在电脑的 GlossX → 设置 → 移动端访问 里刷新二维码再扫一次。"
                                 " / Refresh the QR code in GlossX → Settings → Mobile access and scan again.")
            return await _respond(send, status, body, b"text/html; charset=utf-8")
        device_token = add_device(headers.get("user-agent", ""))
        # the token stays in the landing URL so a home-screen copy of the page is signed in too
        await _respond(send, 303, b"", b"text/plain", [(b"location", f"/?d={device_token}".encode()), _device_cookie(device_token)])
