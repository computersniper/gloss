"""Mobile access: other devices need the pairing code, this computer does not."""
from __future__ import annotations

import pytest


@pytest.fixture()
def phone(client):
    """A client that looks like a phone on the local network."""
    from fastapi.testclient import TestClient
    from app.main import app

    async def from_lan(scope, receive, send):
        await app({**scope, "client": ("192.168.1.23", 50123)}, receive, send)

    # not entered as a context manager: the `client` fixture already runs the lifespan
    yield TestClient(from_lan)


def test_local_requests_need_no_code(client):
    assert client.get("/api/health").status_code == 200


def test_remote_requests_without_code_are_refused(phone):
    r = phone.get("/api/health")
    assert r.status_code == 401
    assert phone.get("/api/papers").status_code == 401


def test_remote_page_load_with_code_sets_cookie(client, phone):
    code = client.get("/api/remote").json()["token"]
    r = phone.get(f"/api/health?token={code}")
    assert r.status_code == 200
    assert "gloss_token=" in r.headers.get("set-cookie", "")
    # the cookie carries later relative requests
    assert phone.get("/api/papers").status_code == 200


def test_remote_header_and_dashless_code(client, phone):
    code = client.get("/api/remote").json()["token"]
    phone.cookies.clear()
    assert phone.get("/api/health", headers={"X-Gloss-Token": code.replace("-", "").upper()}).status_code == 200
    assert phone.get("/api/health", headers={"Authorization": f"Bearer {code}"}).status_code == 200
    assert phone.get("/api/health", headers={"X-Gloss-Token": "wrong-code1"}).status_code == 401


def test_cors_preflight_passes(phone):
    r = phone.options("/api/health", headers={"Origin": "capacitor://localhost", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200


def test_only_this_computer_sees_or_changes_settings(client, phone):
    code = client.get("/api/remote").json()["token"]
    headers = {"X-Gloss-Token": code}
    assert phone.get("/api/remote", headers=headers).status_code == 403
    assert phone.post("/api/remote", json={"enabled": True}, headers=headers).status_code == 403


def test_enable_and_rotate(client, phone):
    before = client.get("/api/remote").json()
    after = client.post("/api/remote", json={"enabled": True, "rotate_token": True}).json()
    assert after["enabled"] is True and after["token"] != before["token"]
    phone.cookies.clear()
    assert phone.get("/api/health", headers={"X-Gloss-Token": before["token"]}).status_code == 401
    assert phone.get("/api/health", headers={"X-Gloss-Token": after["token"]}).status_code == 200
    client.post("/api/remote", json={"enabled": False})


def test_launch_target_follows_setting(client):
    from app import remote_access

    client.post("/api/remote", json={"enabled": False})
    assert remote_access.launch_target("127.0.0.1", 51234, False) == ("127.0.0.1", 51234)
    client.post("/api/remote", json={"enabled": True, "port": 8123})
    assert remote_access.launch_target("127.0.0.1", 51234, False) == ("0.0.0.0", 8123)
    assert remote_access.launch_target("127.0.0.1", 51234, True) == ("127.0.0.1", 51234)
    client.post("/api/remote", json={"enabled": False, "port": 8010})


def _pair_code(client):
    url = client.get("/api/remote?pair=1").json()["pair_urls"][0]
    return url.split("c=", 1)[1]


def test_qr_pairing_signs_one_device_in_once(client, phone):
    phone.cookies.clear()
    code = _pair_code(client)
    r = phone.get(f"/pair?c={code}", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"}, follow_redirects=False)
    assert r.status_code == 303
    landing = r.headers["location"]
    assert landing.startswith("/?d=")
    assert "gloss_device=" in r.headers.get("set-cookie", "")
    # the cookie signs later requests in
    assert phone.get("/api/papers").status_code == 200
    # single use
    other = phone.get(f"/pair?c={code}", follow_redirects=False)
    assert other.status_code == 410
    devices = client.get("/api/remote").json()["devices"]
    assert any(d["name"] == "iPhone" for d in devices)


def test_home_screen_copy_signs_in_from_the_landing_url(client, phone):
    phone.cookies.clear()
    r = phone.get(f"/pair?c={_pair_code(client)}", follow_redirects=False)
    landing = r.headers["location"]
    phone.cookies.clear()  # iOS home-screen apps start without Safari's cookies
    assert phone.get("/api/papers").status_code == 401
    page = phone.get(landing)
    assert page.status_code == 200
    assert phone.get("/api/papers").status_code == 200


def test_newer_qr_replaces_older_and_expired_codes_fail(client, phone, monkeypatch):
    from app import remote_access

    old = _pair_code(client)
    new = _pair_code(client)
    assert phone.get(f"/pair?c={old}", follow_redirects=False).status_code == 410
    monkeypatch.setattr(remote_access.time, "time", lambda: 10**12)
    assert phone.get(f"/pair?c={new}", follow_redirects=False).status_code == 410


def test_removing_a_device_signs_it_out(client, phone):
    phone.cookies.clear()
    phone.get(f"/pair?c={_pair_code(client)}", follow_redirects=False)
    assert phone.get("/api/papers").status_code == 200
    device = client.get("/api/remote").json()["devices"][-1]
    client.post("/api/remote", json={"remove_device": device["id"]})
    assert phone.get("/api/papers").status_code == 401
    # unpaired page loads explain how to pair instead of raw JSON
    page = phone.get("/")
    assert page.status_code == 401 and "text/html" in page.headers["content-type"]


def test_new_pairing_code_signs_out_all_devices(client, phone):
    phone.cookies.clear()
    phone.get(f"/pair?c={_pair_code(client)}", follow_redirects=False)
    client.post("/api/remote", json={"rotate_token": True})
    assert client.get("/api/remote").json()["devices"] == []
    assert phone.get("/api/papers").status_code == 401


def test_home_screen_assets_need_no_sign_in(phone):
    phone.cookies.clear()
    assert phone.get("/manifest.webmanifest").status_code != 401


# ---- internet access through the Cloudflare tunnel ----------------------------

TUNNEL_HEADERS = {"CF-Connecting-IP": "203.0.113.9", "CF-Ray": "8a1b2c3d4e5f-NRT"}


def test_tunnel_traffic_is_never_treated_as_this_computer(client):
    # cloudflared connects from 127.0.0.1, but the request came from the internet
    assert client.get("/api/papers", headers=TUNNEL_HEADERS).status_code == 401
    assert client.get("/api/remote", headers={**TUNNEL_HEADERS, "X-Gloss-Token": client.get("/api/remote").json()["token"]}).status_code == 403
    page = client.get("/", headers=TUNNEL_HEADERS)
    assert page.status_code == 401


def test_pairing_through_the_tunnel(client):
    code = client.get("/api/remote?pair=1").json()["pair_urls"][0].split("c=", 1)[1]
    r = client.get(f"/pair?c={code}", headers=TUNNEL_HEADERS, follow_redirects=False)
    assert r.status_code == 303
    device_token = r.headers["location"].split("d=", 1)[1]
    assert client.get("/api/papers", headers={**TUNNEL_HEADERS, "Cookie": f"gloss_device={device_token}"}).status_code == 200
    client.post("/api/remote", json={"remove_all_devices": True})


def test_wrong_codes_lock_out_for_a_minute(client, monkeypatch):
    from app import remote_access

    monkeypatch.setattr(remote_access, "_failures", [])
    for _ in range(remote_access.FAIL_LIMIT):
        assert client.get("/api/papers?token=wrong-guess", headers=TUNNEL_HEADERS).status_code == 401
    good = client.get("/api/remote").json()["token"]
    assert client.get(f"/api/papers?token={good}", headers=TUNNEL_HEADERS).status_code == 429
    remote_access._failures.clear()
    assert client.get(f"/api/papers?token={good}", headers=TUNNEL_HEADERS).status_code == 200


def test_tunnel_switch_starts_cloudflared_and_shows_its_address(client, tmp_path, monkeypatch):
    import asyncio
    import sys
    import time
    from app import tunnel

    fake = tmp_path / "cloudflared.py"
    fake.write_text(
        "import sys, time\n"
        "print('INF |  https://quiet-river-demo.trycloudflare.com  |', flush=True)\n"
        "time.sleep(60)\n"
    )
    launcher = tmp_path / "cloudflared"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("GLOSS_CLOUDFLARED", str(launcher))
    # Run the same real fake process on Windows as on POSIX. A #!/bin/sh
    # launcher is not a Win32 executable; patch only the process boundary.
    start_process = asyncio.create_subprocess_exec

    async def start_fake(executable, *args, **kwargs):
        assert executable == str(launcher)
        return await start_process(sys.executable, str(fake), *args, **kwargs)

    monkeypatch.setattr(tunnel.asyncio, "create_subprocess_exec", start_fake)
    try:
        client.post("/api/remote", json={"tunnel": True})
        status = {}
        for _ in range(50):
            status = client.get("/api/remote?pair=1").json()
            if status["tunnel"]["state"] == "running":
                break
            time.sleep(0.1)
        assert status["tunnel"]["url"] == "https://quiet-river-demo.trycloudflare.com"
        assert status["tunnel_pair_url"].startswith("https://quiet-river-demo.trycloudflare.com/pair?c=")
    finally:
        client.post("/api/remote", json={"tunnel": False})
    assert client.get("/api/remote").json()["tunnel"]["state"] == "off"
