"""GlossX FastAPI application.

Mounts all API routers and serves the built React frontend (SPA) from
``frontend/dist``. One process, one port.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .remote_access import RemoteAuthMiddleware
from .agent.adapters.mcp_client import MANAGER as mcp_clients
from .agent.runs import AgentRunManager
from .harness.manager import SessionManager
from .library import store
from .library.import_jobs import ImportJobManager
from .plugins import manager as plugin_manager
from .providers import registry
from .tasks import AITaskManager
from .routers import (
    agent,
    browser,
    ai,
    annotations,
    citations,
    doctor,
    harness,
    mcp,
    papers,
    plugins,
    remote,
    scholar,
    sessions,
    settings,
    skills,
    tasks,
    terminal,
    usage,
)

logger = logging.getLogger(__name__)

def _close_streams_on_exit_signal(app: FastAPI) -> None:
    """Session event streams never end by themselves, and the server waits for
    open connections before it runs the lifespan shutdown. Chain the server's
    exit-signal handlers so the streams are closed as soon as a stop is requested."""
    if threading.current_thread() is not threading.main_thread():
        return
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous = signal.getsignal(signum)
            if not callable(previous):
                continue  # default/ignored: no server handler to chain to

            def handler(sig, frame, _previous=previous):
                try:
                    loop.call_soon_threadsafe(app.state.sessions.close_streams)
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(app.state.browser.close()))
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(app.state.terminals.close()))
                except Exception:  # loop already closed
                    pass
                _previous(sig, frame)

            signal.signal(signum, handler)
        except (ValueError, OSError, RuntimeError):
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .harness import home
    home.migrate()
    store.init_db()
    upgraded_plugins = plugin_manager.upgrade_installed_marketplace_plugins()
    if upgraded_plugins:
        logger.info("Upgraded installed plugins: %s", ", ".join(upgraded_plugins))
    app.state.import_jobs = ImportJobManager(
        download_timeout=papers.IMPORT_DOWNLOAD_TIMEOUT,
        parse_timeout=papers.IMPORT_PARSE_TIMEOUT,
        save_timeout=papers.IMPORT_SAVE_TIMEOUT,
    )
    app.state.ai_tasks = AITaskManager()
    app.state.agent_runs = AgentRunManager(import_jobs=app.state.import_jobs)
    app.state.sessions = SessionManager(
        agent_runs=app.state.agent_runs, import_jobs=app.state.import_jobs
    )
    from .harness.browser.engine import BrowserManager
    app.state.browser = BrowserManager(app.state.sessions)
    app.state.sessions.browser = app.state.browser
    from .harness.terminal import TerminalManager
    app.state.terminals = TerminalManager()
    _close_streams_on_exit_signal(app)
    from . import tunnel
    tunnel.start_if_enabled()  # remote access over the internet, when switched on
    try:
        yield
    finally:
        await tunnel.TUNNEL.stop()
        await app.state.terminals.close()
        # sessions first: their turns may be waiting on workflow runs
        await app.state.sessions.shutdown()
        await app.state.browser.close()
        await app.state.agent_runs.shutdown()
        await mcp_clients.close_all()
        await app.state.ai_tasks.shutdown()
        try:
            await asyncio.wait_for(app.state.import_jobs.shutdown(), timeout=20)
        except TimeoutError:
            # Download, parse and save workers already received their cancel
            # signals. Do not let a pathological filesystem cleanup block the
            # local server from restarting forever.
            logger.error("Timed out while shutting down the paper import queue")


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan)

# Other devices (the mobile app) need the pairing code. Added before CORS so
# CORS wraps it and a refusal still reaches the app as a readable 401.
app.add_middleware(RemoteAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    cfg = config.load_config()
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "provider": cfg.get("provider"),
        "providers": registry.available(),
        "target_language": cfg.get("target_language"),
    }


app.include_router(papers.router)
app.include_router(ai.router)
app.include_router(citations.router)
app.include_router(doctor.router)
app.include_router(scholar.router)
app.include_router(annotations.router)
app.include_router(plugins.router)
app.include_router(skills.router)
app.include_router(settings.router)
app.include_router(usage.router)
app.include_router(tasks.router)
app.include_router(agent.router)
app.include_router(mcp.router)
app.include_router(sessions.router)
app.include_router(harness.router)
app.include_router(harness.workspace_router)
app.include_router(browser.router)
app.include_router(terminal.router)
app.include_router(remote.router)


# ---------------------------------------------------------------------------
# Frontend (SPA) — served last so /api/* wins. If the frontend isn't built yet,
# return a helpful message at "/".
# ---------------------------------------------------------------------------
DIST = config.FRONTEND_DIST

if DIST.exists() and (DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # never shadow the API
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(DIST / "index.html"))
else:
    @app.get("/")
    async def _not_built():
        return JSONResponse(
            {
                "detail": "Frontend not built yet. Run scripts/setup.sh (or "
                "`cd frontend && npm install && npm run build`). API is live under /api.",
                "api_health": "/api/health",
            }
        )
