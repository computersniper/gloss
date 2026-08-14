"""Gloss-Local FastAPI application.

Mounts all API routers and serves the built React frontend (SPA) from
``frontend/dist``. One process, one port.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .library import store
from .library.import_jobs import ImportJobManager
from .plugins import manager as plugin_manager
from .providers import registry
from .tasks import AITaskManager
from .routers import (
    ai,
    annotations,
    citations,
    doctor,
    papers,
    plugins,
    scholar,
    settings,
    skills,
    tasks,
    usage,
)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    try:
        yield
    finally:
        await app.state.ai_tasks.shutdown()
        try:
            await asyncio.wait_for(app.state.import_jobs.shutdown(), timeout=20)
        except TimeoutError:
            # Download, parse and save workers already received their cancel
            # signals. Do not let a pathological filesystem cleanup block the
            # local server from restarting forever.
            logger.error("Timed out while shutting down the paper import queue")


app = FastAPI(title="Gloss-Local", version="1.0.3", lifespan=lifespan)

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
