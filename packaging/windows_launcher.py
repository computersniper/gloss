"""Backend entry point of the Gloss desktop app.

The Electron shell in ``desktop/`` starts this frozen program, waits for the
address it reports, and shows the app in its own window. Run on its own it
serves the app for a regular browser.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any


APP_NAME = "Gloss"
APP_VERSION = "2.0.0-beta.1"
STARTUP_TIMEOUT_SECONDS = 30
MIN_FALLBACK_PORT = 1024
def _ensure_standard_streams() -> None:
    """Give windowed PyInstaller builds safe sinks for CLI-only arguments."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def _prepare_imports() -> None:
    root = _resource_root()
    backend = root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    os.environ.setdefault("GLOSS_FRONTEND_DIST", str(root / "frontend" / "dist"))


def _configure_logging() -> Path:
    from app.config import CACHE_DIR

    log_dir = CACHE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "desktop.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    return log_path


def _show_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    else:
        print(f"{APP_NAME}: {message}", file=sys.stderr)


def _port_available(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.4):
            return False
    except OSError:
        return True


def _select_available_port(host: str, preferred_port: int) -> int:
    """Use the preferred port when possible, otherwise pick the next free one."""
    candidates = range(preferred_port, 65536)
    if preferred_port > MIN_FALLBACK_PORT:
        candidates = (*candidates, *range(MIN_FALLBACK_PORT, preferred_port))

    for port in candidates:
        if _port_available(host, port):
            return port
    raise RuntimeError("GlossX could not find an available local port.")


def _health_url(url: str) -> str:
    return f"{url}/api/health"


def _wait_until_ready(url: str, server_thread: threading.Thread) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline and server_thread.is_alive():
        try:
            with urllib.request.urlopen(_health_url(url), timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_server(app: Any, host: str, port: int) -> tuple[Any, threading.Thread]:
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="glossx-backend", daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: Any, server_thread: threading.Thread) -> None:
    server.should_exit = True
    server_thread.join(timeout=10)


def _run_headless(server: Any, server_thread: threading.Thread) -> int:
    try:
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_server(server, server_thread)
    return 0


def _report_ready(url: str) -> None:
    """Tell the desktop shell where the backend actually listens."""
    ready_file = os.environ.get("GLOSSX_READY_FILE")
    if ready_file:
        Path(ready_file).write_text(url, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glossx-backend",
        description="Start the local Gloss server used by the desktop app.",
    )
    parser.add_argument("--host", default=os.environ.get("GLOSS_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("GLOSS_PORT", "8010"))
    )
    parser.add_argument(
        "--headless",
        "--no-window",
        "--no-browser",
        dest="headless",
        action="store_true",
        help="accepted for compatibility; the backend never opens a window",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--browser-smoke", type=Path, metavar="REPORT", help="verify bundled CDP modules with an isolated browser profile")
    return parser


def _browser_smoke(report_path: Path) -> int:
    import asyncio
    import json
    import tempfile
    from app.harness.browser.process import BrowserProcess
    from app.harness.browser.proxy import GuardProxy

    async def verify() -> dict:
        with tempfile.TemporaryDirectory(prefix="glossx-bundle-browser-") as temporary:
            # Launch exactly as the app does, behind the guard proxy.
            proxy = GuardProxy()
            browser = BrowserProcess(Path(temporary))
            browser.proxy_port = await proxy.start()
            process = None
            try:
                client = await browser.start()
                process = browser.process
                target = await client.call("Target.createTarget", {"url": "about:blank"})
                attached = await client.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
                session_id = attached["sessionId"]
                await client.call("Runtime.evaluate", {"expression": "document.body.innerHTML='<h1>GlossX</h1><button>Bundle verified</button>'"}, session_id=session_id)
                tree = await client.call("Accessibility.getFullAXTree", session_id=session_id)
                assert any(node.get("name", {}).get("value") == "Bundle verified" for node in tree["nodes"]), "AX tree missing fixture button"
                frame = await client.call("Page.captureScreenshot", {"format": "png"}, session_id=session_id)
                assert len(frame.get("data", "")) > 100, "empty browser screenshot"
                result = {"browser": browser.executable.browser, "ax_nodes": len(tree["nodes"]), "screenshot_bytes_base64": len(frame["data"])}
            finally:
                await browser.close()
                await proxy.close()
            assert process is not None and process.returncode is not None, "browser did not exit"
            result["process_exit_code"] = process.returncode
            return result

    try:
        result = {"ok": True, **asyncio.run(verify())}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def main() -> int:
    multiprocessing.freeze_support()
    _ensure_standard_streams()
    args = _parser().parse_args()
    if not 1 <= args.port <= 65535:
        _show_error("Port must be between 1 and 65535.")
        return 2

    _prepare_imports()
    if args.browser_smoke:
        return _browser_smoke(args.browser_smoke)
    log_path = _configure_logging()

    try:
        from app.main import app

        from app import remote_access

        host_explicit = "--host" in sys.argv[1:] or bool(os.environ.get("GLOSS_HOST"))
        args.host, args.port = remote_access.launch_target(args.host, args.port, host_explicit)
        os.environ["GLOSS_BIND_HOST"] = args.host
        requested_port = args.port
        args.port = _select_available_port(args.host, requested_port)
        if args.port != requested_port:
            logging.info(
                "Port %s was occupied; starting GlossX on port %s instead",
                requested_port,
                args.port,
            )

        os.environ["GLOSS_BIND_PORT"] = str(args.port)
        browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
        url = f"http://{browser_host}:{args.port}"
        server, server_thread = _start_server(app, args.host, args.port)
        if not _wait_until_ready(url, server_thread):
            _stop_server(server, server_thread)
            raise RuntimeError("The local GlossX server did not become ready in time.")

        _report_ready(url)
        return _run_headless(server, server_thread)
    except Exception as exc:
        logging.exception("GlossX backend startup failed")
        print(f"GlossX could not start.\n\n{exc}\n\nDetails were written to:\n{log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
