"""MCP client adapter: stdio JSON-RPC 2.0 subset, no third-party dependency.

Servers come from ``connections.mcp_servers`` (``{name, command, args, env,
enabled}``). Each is launched lazily as a child process speaking
newline-delimited JSON-RPC on stdin/stdout. We implement ``initialize``,
``notifications/initialized``, ``tools/list`` (with pagination) and
``tools/call``, answer server ``ping`` requests, and ignore other
notifications. Every tool becomes ``mcp.<server>.<tool>`` (permission
``mcp:call``, trust ``external``). Processes are reused across runs and closed
on shutdown or when their configuration changes.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import time
from typing import Any

from ... import config
from ...platform_support import resolve_cli_command, subprocess_group_options, terminate_process_tree
from ..capabilities import Capability, RunContext, clip_card

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
INIT_TIMEOUT = 30.0
LIST_TIMEOUT = 20.0
CALL_TIMEOUT = 150.0
STREAM_LIMIT = 16 * 1024 * 1024
MAX_TOOLS = 64
RESULT_CHARS = 6000
RETRY_AFTER = 60.0  # seconds before a failed server is started again implicitly


class McpError(RuntimeError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(text)).strip("_")[:64]


def _command(cfg: dict[str, Any]) -> list[str]:
    command = cfg["command"]
    if os.path.isabs(command) or "/" in command or "\\" in command:
        if not os.path.exists(command):
            raise FileNotFoundError(f"MCP server command not found: {command}")
        cmd = [command]
    else:
        cmd = resolve_cli_command(command)
    return cmd + [str(a) for a in cfg.get("args") or []]


class McpServerClient:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = dict(cfg)
        self.name = cfg["name"]
        self.proc: asyncio.subprocess.Process | None = None
        self.tools: list[dict[str, Any]] | None = None
        self.server_info: dict[str, Any] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._closed = False

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self._closed

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)[-1500:]

    async def start(self) -> None:
        cmd = _command(self.cfg)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (self.cfg.get("env") or {}).items()})
        cwd = config.DATA_DIR / "mcp" / _slug(self.name)
        cwd.mkdir(parents=True, exist_ok=True)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd),
            limit=STREAM_LIMIT,
            **subprocess_group_options(),
        )
        self._tasks = [
            asyncio.create_task(self._read_loop(), name=f"mcp-{self.name}-stdout"),
            asyncio.create_task(self._drain_stderr(), name=f"mcp-{self.name}-stderr"),
        ]
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "GlossX", "version": "1.0"},
            },
            timeout=INIT_TIMEOUT,
        )
        self.server_info = (result.get("serverInfo") or {}) if isinstance(result, dict) else {}
        await self.notify("notifications/initialized")

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.alive or self.proc is None or self.proc.stdin is None:
            raise McpError(f"MCP server '{self.name}' is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None, *, timeout: float) -> Any:
        rid = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self._send(message)
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise McpError(f"MCP server '{self.name}' did not answer {method} within {int(timeout)}s") from None
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        reason = "MCP server exited"
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except ValueError:  # a single line beyond STREAM_LIMIT
                    reason = "MCP server sent an oversized message"
                    break
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue  # servers sometimes log to stdout; ignore non-JSON lines
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    await self._answer_server_request(msg)
                elif "id" in msg and ("result" in msg or "error" in msg):
                    future = self._pending.get(msg.get("id"))
                    if future is None or future.done():
                        continue
                    if "error" in msg:
                        error = msg.get("error") or {}
                        text = error.get("message") if isinstance(error, dict) else str(error)
                        future.set_exception(McpError(f"{self.name}: {text or 'error'}"))
                    else:
                        future.set_result(msg.get("result"))
                # notifications are ignored
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("MCP reader for %s failed", self.name)
        tail = self.stderr_tail()
        error = McpError(f"{reason}: {tail[-500:]}" if tail else reason)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    async def _answer_server_request(self, msg: dict[str, Any]) -> None:
        if msg.get("method") == "ping":
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": "method not supported by GlossX"}}
        try:
            await self._send(reply)
        except Exception:
            pass

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                self._stderr.append(line.decode("utf-8", errors="replace").rstrip()[:500])
        except (asyncio.CancelledError, ValueError):
            return

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {},
                                        timeout=LIST_TIMEOUT)
            result = result if isinstance(result, dict) else {}
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name"))
            cursor = result.get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        self.tools = tools[:MAX_TOOLS]
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.request("tools/call", {"name": name, "arguments": arguments},
                                    timeout=CALL_TIMEOUT - 5)
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self.proc is not None and self.proc.returncode is None:
            try:
                await terminate_process_tree(self.proc)
            except Exception:  # pragma: no cover - best effort
                logger.exception("could not stop MCP server %s", self.name)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(McpError(f"MCP server '{self.name}' was closed"))


def _content_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or res.get("uri") or "[resource]"))
        elif kind == "resource_link":
            parts.append(f"[resource {item.get('uri') or ''}]")
        else:
            parts.append(f"[{kind or 'content'}]")
    text = "\n".join(p for p in parts if p)
    if not text and result.get("structuredContent") is not None:
        text = json.dumps(result["structuredContent"], ensure_ascii=False, default=str)
    return text


def _schema_text(tool: dict[str, Any]) -> dict[str, str]:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    out: dict[str, str] = {}
    if isinstance(props, dict):
        for key, spec in list(props.items())[:20]:
            spec = spec if isinstance(spec, dict) else {}
            kind = spec.get("type") or "any"
            if isinstance(kind, list):
                kind = "|".join(str(k) for k in kind)
            need = "" if key in required else " (optional)"
            desc = " ".join(str(spec.get("description") or "").split())[:160]
            out[str(key)] = f"{kind}{need}: {desc}" if desc else f"{kind}{need}"
    return out


class McpManager:
    """Process-wide registry of running MCP server clients."""

    def __init__(self) -> None:
        self.clients: dict[str, McpServerClient] = {}
        self.errors: dict[str, str] = {}
        self._failed: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    async def get(self, cfg: dict[str, Any]) -> McpServerClient:
        name = cfg["name"]
        async with self._lock(name):
            client = self.clients.get(name)
            if client is not None and client.alive and client.cfg == cfg and client.tools is not None:
                return client
            if client is not None:
                await client.close()
                self.clients.pop(name, None)
            client = McpServerClient(cfg)
            try:
                await client.start()
                await client.list_tools()
            except BaseException as error:
                await client.close()
                tail = client.stderr_tail()
                message = str(error) or type(error).__name__
                if tail and tail not in message:
                    message = f"{message} | stderr: {tail[-300:]}"
                self.errors[name] = message[:800]
                self._failed[name] = (time.monotonic(), dict(cfg))
                raise
            self.errors.pop(name, None)
            self._failed.pop(name, None)
            self.clients[name] = client
            return client

    async def prune(self, conns: dict[str, Any]) -> None:
        """Close clients whose server was removed, disabled or reconfigured."""
        wanted = {s["name"]: s for s in conns.get("mcp_servers") or [] if s.get("enabled")}
        for name, client in list(self.clients.items()):
            if wanted.get(name) != client.cfg:
                async with self._lock(name):
                    await client.close()
                    self.clients.pop(name, None)
        for name in list(self.errors):
            if name not in wanted:
                self.errors.pop(name, None)

    def _backing_off(self, cfg: dict[str, Any]) -> bool:
        failed = self._failed.get(cfg["name"])
        return bool(failed and failed[1] == cfg and time.monotonic() - failed[0] < RETRY_AFTER)

    async def refresh(self, conns: dict[str, Any], *, force: bool = False) -> None:
        """Start enabled servers that are not running yet (lazily, reused).

        Servers that failed recently are retried only after ``RETRY_AFTER``
        seconds unless ``force`` is set (the status endpoint forces a retry).
        """
        await self.prune(conns)
        servers = [
            s for s in conns.get("mcp_servers") or []
            if s.get("enabled") and (force or not self._backing_off(s))
        ]

        async def one(cfg: dict[str, Any]) -> None:
            try:
                await asyncio.wait_for(self.get(cfg), INIT_TIMEOUT + LIST_TIMEOUT)
            except Exception as error:
                self.errors.setdefault(cfg["name"], (str(error) or type(error).__name__)[:800])
                self._failed.setdefault(cfg["name"], (time.monotonic(), dict(cfg)))
                logger.warning("MCP server %s unavailable: %s", cfg["name"], self.errors[cfg["name"]])

        if servers:
            await asyncio.gather(*(one(s) for s in servers))

    async def status(self, conns: dict[str, Any]) -> list[dict[str, Any]]:
        await self.refresh(conns, force=True)
        out = []
        for cfg in conns.get("mcp_servers") or []:
            name = cfg["name"]
            client = self.clients.get(name)
            if not cfg.get("enabled"):
                out.append({"name": name, "ok": False, "tools": [], "error": "disabled"})
            elif client is not None and client.alive and client.tools is not None:
                out.append({"name": name, "ok": True,
                            "tools": [t["name"] for t in client.tools], "error": None})
            else:
                out.append({"name": name, "ok": False, "tools": [],
                            "error": self.errors.get(name) or "not running"})
        return out

    def capabilities(self, conns: dict[str, Any]) -> list[Capability]:
        caps: list[Capability] = []
        for cfg in conns.get("mcp_servers") or []:
            if not cfg.get("enabled"):
                continue
            client = self.clients.get(cfg["name"])
            if client is None or not client.alive or client.tools is None or client.cfg != cfg:
                continue
            server = _slug(cfg["name"])
            for tool in client.tools:
                tool_name = str(tool["name"])
                caps.append(Capability(
                    name=f"mcp.{server}.{_slug(tool_name)}",
                    kind="tool",
                    card=clip_card(tool.get("description") or tool.get("title") or "")
                    or f"Call the {tool_name} tool of the {cfg['name']} MCP server.",
                    input_schema=_schema_text(tool),
                    handler=self._handler(cfg, tool_name),
                    permissions=["mcp:call"],
                    cost="network",
                    source="mcp",
                    trust="external",
                    timeout=CALL_TIMEOUT,
                    meta={"server": cfg["name"], "tool": tool_name},
                    requirements=[f"MCP server {cfg['name']}"],
                    side_effects="whatever the external MCP tool does",
                ))
        return caps

    def _handler(self, cfg: dict[str, Any], tool_name: str):
        async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
            client = await self.get(cfg)
            result = await client.call_tool(tool_name, dict(args or {}))
            text = _content_text(result)
            if result.get("isError"):
                raise McpError(f"{cfg['name']}.{tool_name} failed: {text[:500] or 'tool error'}")
            return {
                "server": cfg["name"],
                "tool": tool_name,
                "content": text[:RESULT_CHARS] + (" ...[truncated]" if len(text) > RESULT_CHARS else ""),
            }

        return handler

    async def close_all(self) -> None:
        for name, client in list(self.clients.items()):
            try:
                await client.close()
            except Exception:  # pragma: no cover
                logger.exception("could not close MCP server %s", name)
        self.clients.clear()


MANAGER = McpManager()
