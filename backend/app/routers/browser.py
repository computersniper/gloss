"""Built-in Chromium status, session controls and JPEG screencast SSE."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..harness import workspace
from ..harness.browser.cdp import CDPError

router = APIRouter(tags=["browser"])


def manager(request: Request, sid: str | None = None):
    browser = request.app.state.browser
    if sid:
        try:
            browser.sessions._rt(sid)
        except LookupError as error:
            raise HTTPException(404, "Session not found") from error
    return browser


@router.get("/api/browser/status")
async def status(request: Request):
    try:
        return await manager(request).status()
    except ValueError as error:
        return {"available": False, "running": False, "reason": str(error),
                "browser": None, "path": None, "headed": False}


@router.get("/api/sessions/{sid}/browser")
async def session_view(sid: str, request: Request):
    return manager(request, sid).view(sid)


@router.post("/api/sessions/{sid}/browser/input")
async def browser_input(sid: str, body: dict, request: Request):
    action = body.get("type") or body.get("action")
    allowed = {"navigate", "open", "click", "scroll", "key", "press", "text", "type",
               "back", "forward", "reload", "new_tab", "close_tab", "switch_tab", "takeover",
               "read", "import_paper", "snapshot", "viewport"}
    if action not in allowed:
        raise HTTPException(400, "Unknown browser input type")
    try:
        if action == "viewport":
            try:
                width, height = int(body["width"]), int(body["height"])
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(400, "Viewport needs integer width and height") from error
            return await manager(request, sid).set_viewport(sid, width, height)
        return await manager(request, sid).action(sid, action, body, user=True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except CDPError as error:
        raise HTTPException(409, str(error)) from error
    except TimeoutError as error:
        raise HTTPException(504, "Browser operation timed out; retry or reload") from error


@router.get("/api/sessions/{sid}/browser/read")
async def read(sid: str, request: Request, page: int = 1):
    try:
        return await manager(request, sid).action(sid, "read", {"page": page}, user=True)
    except (CDPError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    except TimeoutError as error:
        raise HTTPException(504, "Browser read timed out") from error


@router.get("/api/sessions/{sid}/browser/files/{name}")
async def file(sid: str, name: str, request: Request):
    runtime = manager(request, sid).sessions._rt(sid)
    if Path(name).name != name:
        raise HTTPException(400, "Invalid filename")
    root = workspace.root_for(runtime.session.get("project_id"))
    target = workspace.resolve(root, "downloads/" + name)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    if name.startswith("screenshot-") and name.endswith(".png"):
        return FileResponse(target, media_type="image/png")
    # Downloaded web content stays an attachment, never an executable document
    # served in the application's origin.
    return FileResponse(target, media_type="application/octet-stream", filename=target.name)


@router.get("/api/sessions/{sid}/browser/screencast")
async def screencast(sid: str, request: Request):
    browser = manager(request, sid)

    async def stream():
        try:
            async for frame in browser.frames(sid):
                if await request.is_disconnected():
                    return
                if frame is None:
                    yield ": ping\n\n"
                else:
                    yield "event: browser.frame\ndata: " + json.dumps(frame) + "\n\n"
        except (CDPError, ValueError, TimeoutError) as error:
            yield "event: browser.error\ndata: " + json.dumps({"message": str(error)}) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
