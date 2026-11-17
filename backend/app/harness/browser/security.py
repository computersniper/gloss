"""URL admission checks. Call for *every* intercepted request/redirect, not just open.

DNS admission alone cannot prevent rebinding between this check and Chromium's
resolver. The engine must additionally enforce the network policy (and disable
WebRTC/non-HTTP protocols) before exposing browsing tools.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit


class BrowserSecurityError(ValueError):
    pass


_dns_cache = {}


async def resolve_addresses(host: str, port: int) -> list[tuple]:
    """Resolve normally; recover real addresses from a system TUN fake-IP DNS.

    Benchmark-range fake addresses are never admitted as public sockets. For a
    hostname using that range, resolve its real public records through DoH and
    still enforce public-address admission before connecting numerically.
    """
    results = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM), timeout=5)
    fake = ipaddress.ip_network('198.18.0.0/15')
    if any(ipaddress.ip_address(item[4][0]) in fake for item in results):
        try:
            ipaddress.ip_address(host)
            return results  # A literal private/benchmark address remains blocked.
        except ValueError:
            pass
        hit = _dns_cache.get(host)
        if hit and time.monotonic() - hit[0] < 60:
            addresses = hit[1]
        else:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get('https://1.1.1.1/dns-query', params={'name': host, 'type': 'A'},
                                            headers={'accept': 'application/dns-json'})
                response.raise_for_status()
                addresses = [a['data'] for a in response.json().get('Answer', []) if a.get('type') == 1]
            if not addresses:
                raise BrowserSecurityError('Could not resolve real browser destination behind fake-IP DNS')
            _dns_cache[host] = (time.monotonic(), addresses)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port)) for address in addresses]
    return results


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


async def validate_url(url: str, *, allow_private: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 8192 or not url:
        raise BrowserSecurityError("Expected a nonempty URL (at most 8192 characters)")
    if any(ord(char) < 32 for char in url) or "\\" in url:
        raise BrowserSecurityError("URL contains invalid characters")
    if url == "about:blank":
        return url
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise BrowserSecurityError("Invalid URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserSecurityError("Only http, https and about:blank are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserSecurityError("Credentials in browser URLs are not allowed")
    if port == 0:
        raise BrowserSecurityError("Invalid URL port")
    if allow_private:
        return url
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host and ":" not in host:
        raise BrowserSecurityError("Local/private network URLs are blocked (browser.allow_private is off)")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            results = await resolve_addresses(host, port or (443 if parsed.scheme == "https" else 80))
        except (OSError, asyncio.TimeoutError) as error:
            raise BrowserSecurityError("Could not safely resolve browser URL") from error
        addresses = [item[4][0] for item in results]
    if not addresses or any(not _public(address) for address in addresses):
        raise BrowserSecurityError("Local/private network URLs are blocked (browser.allow_private is off)")
    return url
