"""``web_search`` and ``web_fetch``: papers + open web, degrading gracefully offline."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from ..net import UA, _http_proxy, external_client
from ..search import scholar

MAX_DOWNLOAD = 3_000_000
DEFAULT_CHARS = 20_000
MAX_CHARS = 100_000
DDG_URL = "https://html.duckduckgo.com/html/?q="
_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|td)>', re.S)
_TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment)).split())


def _err(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:200] if str(error) else type(error).__name__


async def search_web(query: str, k: int = 8) -> list[dict[str, Any]]:
    async with external_client(timeout=15) as client:
        r = await client.get(DDG_URL + quote_plus(query))
        r.raise_for_status()
        page = r.text
    links = _RESULT.findall(page)
    snippets = [_clean(s) for s in _SNIPPET.findall(page)]
    out = []
    for i, (href, title) in enumerate(links[:k]):
        href = html.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            href = (parse_qs(parsed.query).get("uddg") or [href])[0]
        out.append({"title": _clean(title), "url": href,
                    "snippet": snippets[i] if i < len(snippets) else ""})
    return out


async def web_search(query: str, *, source: str = "all", k: int = 8) -> dict[str, Any]:
    out: dict[str, Any] = {"query": query, "papers": [], "web": [], "errors": []}

    async def papers() -> None:
        try:
            results = await asyncio.wait_for(scholar.search(query, k), 30)
        except Exception as error:
            out["errors"].append(f"papers: {_err(error)}")
            return
        out["papers"] = [{
            "title": r.get("title", ""), "year": r.get("year"),
            "authors": list(r.get("authors") or [])[:3],
            "abstract": (r.get("abstract") or "")[:300],
            "arxiv_id": r.get("arxiv_id", ""), "doi": r.get("doi", ""), "url": r.get("url", ""),
            "source": r.get("source", ""),
        } for r in results[:k]]

    async def web() -> None:
        try:
            out["web"] = await asyncio.wait_for(search_web(query, k), 20)
        except Exception as error:
            out["errors"].append(f"web: {_err(error)}")

    jobs = []
    if source in {"all", "papers"}:
        jobs.append(papers())
    if source in {"all", "web"}:
        jobs.append(web())
    await asyncio.gather(*jobs)
    if not out["papers"] and not out["web"] and out["errors"]:
        out["note"] = "search is unavailable (offline or blocked); answer from the library instead"
    return out


class _Readable(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "header", "footer", "pre",
             "blockquote", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag in {"h1", "h2", "h3"}:
                self.parts.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = " ".join(data.split())
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        out, blank = [], 0
        for line in lines:
            if not line:
                blank += 1
                if blank <= 1:
                    out.append("")
                continue
            blank = 0
            out.append(line)
        return "\n".join(out).strip()


def html_to_text(page: str) -> tuple[str, str]:
    parser = _Readable()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        return "", _clean(page)
    return parser.title, parser.text()


async def _host_is_private(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            # unresolvable locally (e.g. only the proxy can resolve it): not a local address
            return False
        addrs = []
        for info in infos:
            try:
                addrs.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
               or a.is_multicast or a.is_unspecified for a in addrs)


async def web_fetch(url: str, *, max_chars: int = DEFAULT_CHARS,
                    allow_private: bool = False) -> dict[str, Any]:
    url = str(url or "").strip()
    max_chars = max(500, min(MAX_CHARS, int(max_chars or DEFAULT_CHARS)))
    current = url
    async with _client() as client:
        for _hop in range(6):
            parsed = urlparse(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("only http(s) URLs can be fetched")
            if not allow_private and await _host_is_private(parsed.hostname):
                raise ValueError("refusing to fetch a local or private-network address")
            async with client.stream("GET", current) as r:
                if r.status_code in {301, 302, 303, 307, 308} and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype in {"application/pdf"}:
                    return {"url": url, "final_url": current, "status": r.status_code,
                            "content_type": ctype, "title": "", "text": "",
                            "note": "this is a PDF; import it with library_import to read it"}
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOWNLOAD:
                        break
                encoding = r.encoding or "utf-8"
                status = r.status_code
            break
        else:
            raise ValueError("too many redirects")
    text = bytes(body).decode(encoding, errors="replace")
    title = ""
    if "html" in ctype or (not ctype and "<html" in text[:2000].lower()):
        title, text = html_to_text(text)
    elif ctype and not (ctype.startswith("text/") or ctype in {
            "application/json", "application/xml", "application/xhtml+xml",
            "application/javascript", "application/x-yaml"}):
        return {"url": url, "final_url": current, "status": status, "content_type": ctype,
                "title": "", "text": "", "note": f"unsupported content type {ctype}"}
    truncated = len(text) > max_chars
    return {"url": url, "final_url": current, "status": status, "content_type": ctype,
            "title": title, "text": text[:max_chars], "truncated": truncated,
            "total_chars": len(text)}


def _client():
    import httpx

    return httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=False, trust_env=False,
                             proxy=_http_proxy())
