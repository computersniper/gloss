"""Private-network guard at the actual socket, including DNS rebinding.

Chromium uses this local proxy for every HTTP(S) connection. DNS is resolved
once and sockets connect to admitted numeric addresses, never resolving again.
"""
from __future__ import annotations
import asyncio
import socket
from urllib.parse import urlsplit
from .security import BrowserSecurityError, _public, validate_url, resolve_addresses

class GuardProxy:

    def __init__(self, allow_private=False):
        self.allow_private = allow_private
        self.server = None
        self.tasks = set()
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_server(self._client, '127.0.0.1', 0, limit=65536)
        return self.server.sockets[0].getsockname()[1]

    async def _connect(self, host, port):
        addresses = await resolve_addresses(host, port)
        if not addresses or (not self.allow_private and any((not _public(a[4][0]) for a in addresses))):
            raise BrowserSecurityError('Private network connection blocked')
        last = None
        for address in addresses:
            try:
                # Numeric address pins DNS admission to the actual destination.
                return await asyncio.wait_for(asyncio.open_connection(address[4][0], port, family=address[0]), 10)
            except OSError as error:
                last = error
        raise last or OSError('No usable public address')

    async def _client(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        remote = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            method, target, version = header.split(b'\r\n', 1)[0].decode('ascii').split(' ', 2)
            if method == 'CONNECT':
                url = 'https://' + target
                await validate_url(url, allow_private=self.allow_private)
                parsed = urlsplit(url)
                upstream, remote = await self._connect(parsed.hostname, parsed.port or 443)
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            else:
                await validate_url(target, allow_private=self.allow_private)
                parsed = urlsplit(target)
                if parsed.scheme != 'http':
                    raise BrowserSecurityError('Expected HTTP proxy request')
                upstream, remote = await self._connect(parsed.hostname, parsed.port or 80)
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                rest = header.split(b'\r\n', 1)[1]
                # Ask the origin to close each request, so a subsequent request
                # for another host cannot ride an already-admitted socket.
                headers = [line for line in rest.split(b'\r\n') if line and (not line.lower().startswith((b'proxy-', b'connection:')))]
                remote.write(f'{method} {path} {version}\r\n'.encode() + b'\r\n'.join(headers) + b'\r\nConnection: close\r\n\r\n')
                await remote.drain()
            await writer.drain()
            self.writers.add(remote)

            async def copy(source, destination):
                while (data := (await source.read(65536))):
                    destination.write(data)
                    await destination.drain()
            a = asyncio.create_task(copy(reader, remote))
            b = asyncio.create_task(copy(upstream, writer))
            try:
                await asyncio.wait([a, b], return_when=asyncio.FIRST_COMPLETED)
            finally:
                a.cancel()
                b.cancel()
                await asyncio.gather(a, b, return_exceptions=True)
        except (Exception, asyncio.CancelledError):
            try:
                writer.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
            except Exception:
                pass
        finally:
            for connection in (writer, remote):
                if connection:
                    self.writers.discard(connection)
                    connection.close()
            self.tasks.discard(task)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for writer in list(self.writers):
            writer.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
