"""Offline browser primitives; optional installed-Chromium smoke test."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.harness.browser.cdp import CDPClient, CDPError
from app.harness.browser.discovery import discover
from app.harness.browser.process import BrowserProcess
from app.harness.browser.security import BrowserSecurityError, validate_url
from app.harness.browser.snapshot import SnapshotReferences, StaleReferenceError


def _node(node_id, role, name="", children=(), **extra):
    return {"nodeId": str(node_id), "role": {"value": role}, "name": {"value": name},
            "childIds": [str(child) for child in children], **extra}


def test_snapshot_outline_urls_password_and_stale_refs():
    refs = SnapshotReferences()
    nodes = [_node(1, "RootWebArea", "Example", [2, 3, 4]),
             _node(2, "link", "Paper", backendDOMNodeId=12),
             _node(3, "textbox", "Password", backendDOMNodeId=13,
                   value={"value": "secret"}, properties=[{"name": "protected", "value": {"value": True}}]),
             _node(4, "StaticText", "Article body", backendDOMNodeId=14)]
    snapshot = refs.render(nodes, urls={12: "https://example.org/paper"})
    assert '  [e1] link "Paper" url="https://example.org/paper"' in snapshot["text"]
    assert "secret" not in snapshot["text"]
    assert refs.resolve("e2").password
    assert refs.resolve("[e1]").backend_node_id == 12
    refs.render(nodes, find="Article")
    with pytest.raises(StaleReferenceError, match="browser_snapshot"):
        refs.resolve("e1")
    assert refs.resolve("e4").backend_node_id == 14
    refs.invalidate()
    with pytest.raises(StaleReferenceError):
        refs.resolve("e4")


def test_snapshot_limits_subtrees_and_ignored_nodes():
    refs = SnapshotReferences()
    nodes = [_node(1, "RootWebArea", "Page", [2]), _node(2, "generic", "", [3], ignored=True),
             _node(3, "button", "More", [4], backendDOMNodeId=3),
             _node(4, "StaticText", "A" * 1000, backendDOMNodeId=4)]
    initial = refs.render(nodes)
    assert "generic" not in initial["text"]
    assert '  [e1] button "More"' in initial["text"]
    subtree = refs.render(nodes, ref="e1")
    assert "RootWebArea" not in subtree["text"]
    assert "More" in subtree["text"]
    assert len(refs.render(nodes, max_chars=200)["text"]) <= 200
    assert refs.render(nodes, max_chars=200)["truncated"]
    assert "Snapshot truncated" in refs.render(nodes, max_chars=200)["text"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "chrome://settings", "data:text/html,hi",
    "javascript:alert(1)", "http://localhost/", "http://127.0.0.1/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://10.2.3.4/", "http://192.168.1.1/",
    "http://169.254.169.254/", "http://intranet/", "http://host.local/",
    "http://user:password@example.org/", "https://example.org\\@127.0.0.1/",
    "http://example.org:0/", "https://example.org/\n"])
def test_browser_rejects_unsafe_urls(url):
    with pytest.raises(BrowserSecurityError):
        asyncio.run(validate_url(url))


def test_browser_dns_mixed_addresses_and_private_override(monkeypatch):
    async def check():
        async def resolve(host, port, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", port)), (2, 1, 6, "", ("10.0.0.1", port))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(BrowserSecurityError, match="private"):
            await validate_url("https://example.org")
        assert await validate_url("http://127.0.0.1", allow_private=True)
        assert await validate_url("about:blank") == "about:blank"
        with pytest.raises(BrowserSecurityError):
            await validate_url("file:///a", allow_private=True)
        assert await validate_url("https://93.184.216.34")
    asyncio.run(check())


def test_discovery_override_and_windows_paths(tmp_path):
    with pytest.raises(ValueError, match="browser.path"):
        discover(str(tmp_path / "missing"))
    edge = tmp_path / "Microsoft/Edge/Application/msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.touch()
    browser = discover(platform="win32", env={"PROGRAMFILES": str(tmp_path)})
    # Registry paths may precede the synthetic path on a real Windows machine.
    assert browser is not None
    assert discover(str(edge), platform="win32").browser == "Edge"
    binary = tmp_path / "chromium"
    binary.touch()
    binary.chmod(0o755)
    assert discover(str(binary), platform="linux").browser == "Chromium"


class _Socket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
    def __aiter__(self):
        return self
    async def __anext__(self):
        message = await self.messages.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)
    async def send(self, data):
        self.sent.append(json.loads(data))
    async def close(self):
        self.messages.put_nowait(None)


def test_cdp_multiplex_events_timeout_errors_and_disconnect():
    async def check():
        socket = _Socket()
        client = CDPClient(socket)
        try:
            first = asyncio.create_task(client.call("Page.enable", session_id="one"))
            second = asyncio.create_task(client.call("DOM.enable", session_id="two"))
            await asyncio.sleep(0)
            assert socket.sent[0]["sessionId"] == "one"
            await socket.messages.put({"id": 2, "result": {"ok": 2}})
            await socket.messages.put({"id": 1, "result": {"ok": 1}})
            assert await second == {"ok": 2}
            assert await first == {"ok": 1}
            async with client.events("Page.loadEventFired", session_id="one", maxsize=1) as queue:
                await socket.messages.put({"method": "Page.loadEventFired", "sessionId": "two", "params": {"wrong": True}})
                await socket.messages.put({"method": "Page.loadEventFired", "sessionId": "one", "params": {"timestamp": 1}})
                assert await asyncio.wait_for(queue.get(), 1) == {"timestamp": 1}
            with pytest.raises(TimeoutError):
                await client.call("Never.answers", timeout=0.01)
            assert not client._pending
            error = asyncio.create_task(client.call("Bad.method"))
            await asyncio.sleep(0)
            await socket.messages.put({"id": socket.sent[-1]["id"], "error": {"code": -1, "message": "missing"}})
            with pytest.raises(CDPError, match="missing"):
                await error
            pending = asyncio.create_task(client.call("Pending.method"))
            await asyncio.sleep(0)
            await socket.close()
            with pytest.raises(CDPError, match="closed"):
                await pending
        finally:
            await client.close()
    asyncio.run(check())


def test_installed_browser_cdp_and_process_cleanup(tmp_path):
    # An installed executable does not imply a usable Chromium sandbox (for
    # example on hosted Linux runners). Keep real-browser integration opt-in,
    # like the real-provider and network suites; offline CDP tests still run.
    if os.environ.get("GLOSS_TEST_BROWSER") != "1" and not os.environ.get("GLOSS_TEST_BROWSER_PATH"):
        pytest.skip("Real browser not enabled (set GLOSS_TEST_BROWSER=1 or GLOSS_TEST_BROWSER_PATH)")
    executable = discover(os.environ.get("GLOSS_TEST_BROWSER_PATH"))
    if executable is None:
        pytest.skip("No installed Chromium browser")
    async def check():
        browser = BrowserProcess(tmp_path, path=str(executable.path))
        process = None
        try:
            client = await browser.start()
            process = browser.process
            target = await client.call("Target.createTarget", {"url": "about:blank"})
            attached = await client.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
            sid = attached["sessionId"]
            await client.call("Runtime.evaluate", {"expression": "document.body.innerHTML='<main><h1>Browser smoke</h1><button>Continue</button></main>'"}, session_id=sid)
            await client.call("Accessibility.enable", session_id=sid)
            ax = await client.call("Accessibility.getFullAXTree", session_id=sid)
            snapshot = SnapshotReferences().render(ax["nodes"])
            assert 'button "Continue"' in snapshot["text"]
            assert "Browser smoke" in snapshot["text"]
            image = await client.call("Page.captureScreenshot", {"format": "jpeg"}, session_id=sid)
            assert len(image["data"]) > 100
            assert browser.profile.parent.parent == tmp_path.resolve()
        finally:
            await browser.close()
        assert process is not None and process.returncode is not None
    asyncio.run(check())
