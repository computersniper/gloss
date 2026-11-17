"""MCP server: ``POST /mcp`` speaks JSON-RPC 2.0 (MCP over HTTP) so Claude
Code, Codex and other MCP clients can use the GlossX library and agent.

Implemented methods: ``initialize``, ``notifications/*`` (accepted, no reply),
``ping``, ``tools/list`` and ``tools/call``. Tools:
``glossx_list_papers``, ``glossx_search_spans``, ``glossx_start_run`` and
``glossx_get_run``. Malformed JSON-RPC envelopes get HTTP 400; method-level
errors are JSON-RPC error objects; tool failures are ``isError`` results.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..agent.capabilities import bm25_rank
from ..agent.context import SpanStore
from ..agent.runs import RunNotFoundError
from ..config import APP_NAME
from ..library import store

router = APIRouter(tags=["mcp"])

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_VERSION = "1.0.2"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "glossx_list_papers",
        "description": "List papers in the GlossX library (id, title, year, authors). Optionally filter by a keyword query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional keywords matched against title and abstract."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Maximum papers (default 50)."},
            },
        },
    },
    {
        "name": "glossx_search_spans",
        "description": "Search citable evidence passages (span id, paper, page, text) in library papers by keywords.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords or a question."},
                "paper_ids": {"type": "array", "items": {"type": "string"},
                              "description": "Papers to search; defaults to the 50 most recent."},
                "k": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Number of spans (default 6)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "glossx_start_run",
        "description": "Start a GlossX research agent run on a goal; returns the run id and status. Poll with glossx_get_run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The research question or task."},
                "paper_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional scope papers."},
                "workflow": {"type": "string", "description": "'auto' (default), 'generic' or 'workflow.<id>'."},
                "loop": {"type": "string", "description": "'auto' (default), 'plan-execute-verify' or 'react'."},
                "project_id": {"type": "string", "description": "Optional GlossX project id."},
                "max_rounds": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "glossx_get_run",
        "description": "Get a GlossX agent run: status, route, pending approval, budget and (when finished) the cited answer.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Run id from glossx_start_run."}},
            "required": ["run_id"],
        },
    },
]


class ToolInputError(ValueError):
    pass


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _rpc_result(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _str(args: dict[str, Any], key: str, *, required: bool = False, maximum: int = 4000) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolInputError(f"'{key}' is required")
        return ""
    if not isinstance(value, str):
        raise ToolInputError(f"'{key}' must be a string")
    if len(value) > maximum:
        raise ToolInputError(f"'{key}' is too long")
    return value.strip()


def _int(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"'{key}' must be an integer")
    if not lo <= value <= hi:
        raise ToolInputError(f"'{key}' must be between {lo} and {hi}")
    return value


def _str_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or len(value) > 50:
        raise ToolInputError(f"'{key}' must be a list of at most 50 strings")
    return value


def _brief(p: dict) -> dict[str, Any]:
    return {"id": p.get("id"), "title": p.get("title", ""), "year": p.get("year", ""),
            "authors": list(p.get("authors") or [])[:4]}


def _run_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "goal", "status", "phase", "route", "project_id", "pending_approval",
            "budget", "result", "error", "created_at", "finished_at")
    return {k: snapshot.get(k) for k in keep}


async def _call_tool(name: str, args: dict[str, Any], request: Request) -> Any:
    if name == "glossx_list_papers":
        query = _str(args, "query", maximum=500)
        limit = _int(args, "limit", 50, 1, 200)
        papers = store.list_papers()
        if query:
            ranked = bm25_rank(query, [f"{p.get('title') or ''} {p.get('abstract') or ''}" for p in papers])
            papers = [papers[i] for i, score in ranked if score > 0]
        return {"count": len(papers), "papers": [_brief(p) for p in papers[:limit]]}
    if name == "glossx_search_spans":
        query = _str(args, "query", required=True, maximum=1000)
        k = _int(args, "k", 6, 1, 20)
        paper_ids = _str_list(args, "paper_ids") or [p["id"] for p in store.list_papers()[:50]]
        missing = [pid for pid in paper_ids if not store.get_paper(pid)]
        if missing:
            raise ToolInputError(f"paper not found: {missing[0]}")
        return {"query": query, "spans": SpanStore().search(query, paper_ids, k=k)}
    manager = request.app.state.agent_runs
    if name == "glossx_start_run":
        goal = _str(args, "goal", required=True)
        try:
            snapshot = manager.start(
                goal,
                _str_list(args, "paper_ids"),
                max_rounds=_int(args, "max_rounds", 2, 1, 3),
                project_id=_str(args, "project_id", maximum=64) or None,
                workflow=_str(args, "workflow", maximum=120) or "auto",
                loop=_str(args, "loop", maximum=60) or "auto",
                max_rounds_explicit="max_rounds" in args,
            )
        except (LookupError, ValueError, RuntimeError) as error:
            raise ToolInputError(str(error).strip("'\"")) from error
        return _run_view(snapshot)
    if name == "glossx_get_run":
        run_id = _str(args, "run_id", required=True, maximum=64)
        try:
            return _run_view(manager.get(run_id))
        except RunNotFoundError:
            raise ToolInputError("run not found") from None
    raise LookupError(name)


async def _handle(message: Any, request: Request) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _rpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "invalid request")
    method = message["method"]
    rid = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return None if is_notification else _rpc_error(rid, -32602, "params must be an object")
    if is_notification:
        return None  # notifications/initialized, notifications/cancelled, ...
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _rpc_result(rid, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": APP_NAME, "version": SERVER_VERSION},
            "instructions": (
                "GlossX is a local research-paper library and agent. Use glossx_list_papers and "
                "glossx_search_spans for cited evidence; glossx_start_run starts an agent run "
                "that may pause for approval in the GlossX app."
            ),
        })
    if method == "ping":
        return _rpc_result(rid, {})
    if method == "tools/list":
        return _rpc_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _rpc_error(rid, -32602, "tools/call needs a tool name and an arguments object")
        try:
            result = await _call_tool(name, args, request)
        except LookupError:
            return _rpc_error(rid, -32602, f"unknown tool: {name}")
        except ToolInputError as error:
            return _rpc_result(rid, {"content": [{"type": "text", "text": str(error)}], "isError": True})
        text = json.dumps(result, ensure_ascii=False, default=str)
        return _rpc_result(rid, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        })
    return _rpc_error(rid, -32601, f"method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        payload = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
    if isinstance(payload, list):
        if not payload:
            return JSONResponse(_rpc_error(None, -32600, "empty batch"), status_code=400)
        replies = [r for r in [await _handle(m, request) for m in payload] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        rid = payload.get("id") if isinstance(payload, dict) else None
        return JSONResponse(_rpc_error(rid, -32600, "invalid request"), status_code=400)
    reply = await _handle(payload, request)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.get("/mcp")
async def mcp_get():
    # no server-initiated SSE stream; Streamable HTTP clients accept 405 here
    return Response(status_code=405, headers={"Allow": "POST"})
