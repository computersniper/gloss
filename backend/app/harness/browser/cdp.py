"""Small asynchronous CDP transport with multiplexed commands and bounded events."""
from __future__ import annotations

import asyncio
import json
import inspect
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

# Prefer the current API, retaining uvicorn[standard]'s older supported versions.
try:
    from websockets.asyncio.client import connect
except ImportError:
    from websockets.legacy.client import connect
_CONNECT_OPTIONS = {"proxy": None} if "proxy" in inspect.signature(connect).parameters else {}


class CDPError(RuntimeError):
    pass


class CDPClient:
    def __init__(self, socket: Any):
        self.socket = socket
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: dict[tuple[str, str | None], set[asyncio.Queue]] = defaultdict(set)
        self._closed = False
        self._reader = asyncio.create_task(self._receive(), name="glossx-cdp-reader")

    @classmethod
    async def open(cls, url: str) -> CDPClient:
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise CDPError("CDP debugger must be a local websocket")
        socket = await connect(url, open_timeout=10, max_size=32 * 1024 * 1024, **_CONNECT_OPTIONS)
        return cls(socket)

    async def call(self, method: str, params: dict | None = None, *,
                   session_id: str | None = None, timeout: float = 15) -> dict:
        if self._closed:
            raise CDPError("Browser connection is closed")
        self._next_id += 1
        command_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        message: dict[str, Any] = {"id": command_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        try:
            # The deadline covers send/backpressure as well as the response.
            async with asyncio.timeout(timeout):
                await self.socket.send(json.dumps(message))
                return await future
        finally:
            self._pending.pop(command_id, None)
            if not future.done():
                future.cancel()

    @asynccontextmanager
    async def events(self, method: str, *, session_id: str | None = None, maxsize: int = 32, lossless: bool = False):
        """Subscribe before starting the producer. Slow viewers retain latest events.

        Network interception consumers must size/drain their queues appropriately;
        this lossy queue is intended for view/telemetry, not security decisions.
        """
        key = (method, session_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=0 if lossless else max(1, maxsize))
        self._events[key].add(queue)
        try:
            yield queue
        finally:
            self._events[key].discard(queue)
            if not self._events[key]:
                self._events.pop(key, None)

    async def _receive(self):
        reason = "Browser connection closed"
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.get(message["id"])
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        error = message["error"]
                        future.set_exception(CDPError(f"{error.get('code')}: {error.get('message', 'CDP error')}"))
                    else:
                        future.set_result(message.get("result", {}))
                elif "method" in message:
                    key = (message["method"], message.get("sessionId"))
                    for queue in tuple(self._events.get(key, ())):
                        if queue.full():
                            queue.get_nowait()
                        queue.put_nowait(message.get("params", {}))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            reason = f"Browser connection failed: {error}"
        finally:
            self._closed = True
            for queues in tuple(self._events.values()):
                for queue in tuple(queues):
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(CDPError(reason))

    async def close(self):
        self._closed = True
        try:
            await self.socket.close()
        finally:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
