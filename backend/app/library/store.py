"""SQLite-backed library store + per-paper filesystem storage.

DB holds paper metadata, highlights/annotations, chats/messages, parsed references
and a small feature cache. Large artefacts (original.pdf, parsed.json) live under
``data/papers/<id>/`` on the data disk.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..config import DB_PATH, PAPERS_DIR
from ..platform_support import read_utf8_text

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT,
    authors TEXT,
    year TEXT,
    abstract TEXT,
    source TEXT,
    arxiv_id TEXT,
    doi TEXT,
    n_pages INTEGER,
    tags TEXT DEFAULT '[]',
    reading_lens TEXT NOT NULL DEFAULT 'auto',
    added_at REAL
);
CREATE TABLE IF NOT EXISTS highlights (
    id TEXT PRIMARY KEY,
    paper_id TEXT,
    page INTEGER,
    rects TEXT,
    color TEXT,
    category TEXT,
    text TEXT,
    note TEXT,
    kind TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS drawings (
    id TEXT PRIMARY KEY,
    paper_id TEXT,
    page INTEGER,
    points TEXT,
    color TEXT,
    width REAL,
    tool TEXT NOT NULL DEFAULT 'pen',
    note TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS personal_notes (
    paper_id TEXT PRIMARY KEY,
    content TEXT,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    paper_id TEXT,
    title TEXT,
    provider TEXT,
    provider_session_id TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    role TEXT,
    content TEXT,
    attachments TEXT NOT NULL DEFAULT '[]',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS refs (
    id TEXT PRIMARY KEY,
    paper_id TEXT,
    idx INTEGER,
    raw TEXT,
    title TEXT,
    authors TEXT,
    year TEXT,
    doi TEXT,
    arxiv_id TEXT,
    url TEXT,
    abstract TEXT,
    resolved INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache (
    paper_id TEXT,
    key TEXT,
    value TEXT,
    updated_at REAL,
    PRIMARY KEY (paper_id, key)
);
CREATE TABLE IF NOT EXISTS ai_usage (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    paper_id TEXT,
    plugin_id TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_created_at ON ai_usage(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_usage_task_type ON ai_usage(task_type);
CREATE TABLE IF NOT EXISTS provider_sessions (
    paper_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (paper_id, provider, content_fingerprint)
);
"""


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)
        paper_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(papers)").fetchall()
        }
        if "reading_lens" not in paper_columns:
            con.execute(
                "ALTER TABLE papers ADD COLUMN reading_lens TEXT NOT NULL DEFAULT 'auto'"
            )
        usage_columns = {row["name"] for row in con.execute("PRAGMA table_info(ai_usage)")}
        for column in ("cached_input_tokens", "cache_write_tokens", "reasoning_tokens"):
            if column not in usage_columns:
                con.execute(f"ALTER TABLE ai_usage ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        drawing_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(drawings)").fetchall()
        }
        if "tool" not in drawing_columns:
            con.execute(
                "ALTER TABLE drawings ADD COLUMN tool TEXT NOT NULL DEFAULT 'pen'"
            )
        chat_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(chats)").fetchall()
        }
        if "provider" not in chat_columns:
            con.execute("ALTER TABLE chats ADD COLUMN provider TEXT")
        if "provider_session_id" not in chat_columns:
            con.execute("ALTER TABLE chats ADD COLUMN provider_session_id TEXT")
        message_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "attachments" not in message_columns:
            con.execute(
                "ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'"
            )


# ---------------------------------------------------------------------------
# AI usage accounting
# ---------------------------------------------------------------------------
def record_ai_usage(
    *,
    task_type: str,
    provider: str,
    model: str | None,
    paper_id: str | None,
    plugin_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated: bool,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> dict[str, Any]:
    """Persist a single completed provider invocation.

    ``estimated`` is intentionally stored alongside the count: subscription
    CLIs can omit usage entirely, so estimates must never be confused with an
    API/provider-reported total.
    """
    record = {
        "id": _uid(),
        "task_type": task_type,
        "provider": provider,
        "model": model,
        "paper_id": paper_id,
        "plugin_id": plugin_id,
        "prompt_tokens": max(0, int(prompt_tokens)),
        "completion_tokens": max(0, int(completion_tokens)),
        "total_tokens": max(0, int(total_tokens)),
        "estimated": bool(estimated),
        "cached_input_tokens": max(0, min(int(cached_input_tokens), int(prompt_tokens))),
        "cache_write_tokens": max(0, int(cache_write_tokens)),
        "reasoning_tokens": max(0, int(reasoning_tokens)),
        "created_at": _now(),
    }
    with _conn() as con:
        con.execute(
            """
            INSERT INTO ai_usage (
                id, task_type, provider, model, paper_id, plugin_id,
                prompt_tokens, completion_tokens, total_tokens, estimated, created_at,
                cached_input_tokens, cache_write_tokens, reasoning_tokens
            ) VALUES (
                :id, :task_type, :provider, :model, :paper_id, :plugin_id,
                :prompt_tokens, :completion_tokens, :total_tokens, :estimated, :created_at,
                :cached_input_tokens, :cache_write_tokens, :reasoning_tokens
            )
            """,
            record,
        )
    return record


def ai_usage_summary(
    *, paper_id: str | None = None, task_type: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Return aggregate and recent completed AI-invocation usage records."""
    clauses: list[str] = []
    params: list[Any] = []
    if paper_id:
        clauses.append("paper_id = ?")
        params.append(paper_id)
    if task_type:
        clauses.append("task_type = ?")
        params.append(task_type)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    aggregate = """
        COUNT(*) AS tasks,
        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(AVG(total_tokens), 0) AS average_total_tokens,
        COALESCE(SUM(estimated), 0) AS estimated_tasks
        , COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens
        , COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens
        , COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens
    """
    with _conn() as con:
        total = dict(con.execute(f"SELECT {aggregate} FROM ai_usage{where}", params).fetchone())
        by_task = [
            dict(row)
            for row in con.execute(
                f"SELECT task_type, {aggregate} FROM ai_usage{where} "
                "GROUP BY task_type ORDER BY total_tokens DESC, task_type ASC",
                params,
            ).fetchall()
        ]
        recent = [
            dict(row)
            for row in con.execute(
                f"SELECT * FROM ai_usage{where} ORDER BY created_at DESC LIMIT ?",
                [*params, max(1, min(int(limit), 200))],
            ).fetchall()
        ]
        by_provider = [dict(row) for row in con.execute(
            f"SELECT provider, {aggregate} FROM ai_usage{where} GROUP BY provider ORDER BY total_tokens DESC", params)]
        by_day = [dict(row) for row in con.execute(
            f"SELECT date(created_at, 'unixepoch') AS day, {aggregate} FROM ai_usage{where} GROUP BY day ORDER BY day DESC", params)]
    for row in [total, *by_task, *by_provider, *by_day]:
        row["estimated_tasks"] = int(row["estimated_tasks"])
        row["average_total_tokens"] = round(float(row["average_total_tokens"]), 1)
        row["input_tokens"] = row["prompt_tokens"]
        row["output_tokens"] = row["completion_tokens"]
        row["cache_hit_rate"] = row["cached_input_tokens"] / row["prompt_tokens"] if row["prompt_tokens"] else 0.0
        row["exact"] = row["estimated_tasks"] == 0
    for row in recent:
        row["input_tokens"] = row["prompt_tokens"]
        row["output_tokens"] = row["completion_tokens"]
        row["cache_hit_rate"] = row["cached_input_tokens"] / row["prompt_tokens"] if row["prompt_tokens"] else 0.0
        row["exact"] = not bool(row["estimated"])
    return {"total": total, "by_task": by_task, "by_provider": by_provider, "by_day": by_day, "recent": recent}


# ---------------------------------------------------------------------------
# Shared per-paper provider sessions
# ---------------------------------------------------------------------------
def get_provider_session(
    paper_id: str, provider: str, content_fingerprint: str
) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute(
            """
            SELECT paper_id,provider,content_fingerprint,session_id,created_at,updated_at
            FROM provider_sessions
            WHERE paper_id=? AND provider=? AND content_fingerprint=?
            """,
            (paper_id, provider, content_fingerprint),
        ).fetchone()
    return dict(row) if row else None


def set_provider_session(
    paper_id: str,
    provider: str,
    content_fingerprint: str,
    session_id: str,
) -> dict[str, Any]:
    now = _now()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO provider_sessions (
                paper_id,provider,content_fingerprint,session_id,created_at,updated_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(paper_id,provider,content_fingerprint) DO UPDATE SET
                session_id=excluded.session_id,
                updated_at=excluded.updated_at
            """,
            (paper_id, provider, content_fingerprint, session_id, now, now),
        )
    return get_provider_session(paper_id, provider, content_fingerprint) or {}


def delete_provider_session(
    paper_id: str, provider: str, content_fingerprint: str
) -> None:
    with _conn() as con:
        con.execute(
            """
            DELETE FROM provider_sessions
            WHERE paper_id=? AND provider=? AND content_fingerprint=?
            """,
            (paper_id, provider, content_fingerprint),
        )


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------
def paper_dir(paper_id: str) -> Path:
    d = PAPERS_DIR / paper_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_path(paper_id: str) -> Path:
    return paper_dir(paper_id) / "original.pdf"


def parsed_path(paper_id: str) -> Path:
    return paper_dir(paper_id) / "parsed.json"


def load_parsed(paper_id: str) -> dict | None:
    p = parsed_path(paper_id)
    if p.exists():
        return json.loads(read_utf8_text(p))
    return None


def save_parsed(paper_id: str, parsed: dict) -> None:
    parsed_path(paper_id).write_text(
        json.dumps(parsed, ensure_ascii=False), encoding="utf-8"
    )


def create_paper(meta: dict[str, Any]) -> str:
    pid = meta.get("id") or _uid()
    with _conn() as con:
        con.execute(
            """INSERT INTO papers (id,title,authors,year,abstract,source,arxiv_id,doi,n_pages,tags,reading_lens,added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                meta.get("title", ""),
                json.dumps(meta.get("authors", []), ensure_ascii=False),
                str(meta.get("year", "") or ""),
                meta.get("abstract", ""),
                meta.get("source", "upload"),
                meta.get("arxiv_id", ""),
                meta.get("doi", ""),
                int(meta.get("n_pages", 0) or 0),
                json.dumps(meta.get("tags", []), ensure_ascii=False),
                meta.get("reading_lens", "auto"),
                _now(),
            ),
        )
    return pid


def _row_to_paper(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["authors"] = json.loads(d.get("authors") or "[]")
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["reading_lens"] = d.get("reading_lens") or "auto"
    return d


def get_paper(paper_id: str) -> dict | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        return _row_to_paper(r) if r else None


def list_papers() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM papers ORDER BY added_at DESC").fetchall()
        return [_row_to_paper(r) for r in rows]


def update_paper(paper_id: str, fields: dict[str, Any]) -> None:
    allowed = {
        "title", "authors", "year", "abstract", "tags", "doi", "arxiv_id",
        "reading_lens",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("authors", "tags"):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(paper_id)
    with _conn() as con:
        con.execute(f"UPDATE papers SET {','.join(sets)} WHERE id=?", vals)


def delete_paper(paper_id: str) -> None:
    with _conn() as con:
        for t in (
            "papers", "highlights", "drawings", "personal_notes",
            "chats", "messages", "refs", "cache", "provider_sessions",
        ):
            col = "id" if t == "papers" else "paper_id"
            if t == "messages":
                con.execute(
                    "DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE paper_id=?)",
                    (paper_id,),
                )
                continue
            con.execute(f"DELETE FROM {t} WHERE {col}=?", (paper_id,))
    d = PAPERS_DIR / paper_id
    if d.exists():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


# ---------------------------------------------------------------------------
# Highlights / annotations
# ---------------------------------------------------------------------------
def add_highlight(paper_id: str, h: dict) -> dict:
    hid = h.get("id") or _uid()
    row = (
        hid, paper_id, int(h.get("page", 0)),
        json.dumps(h.get("rects", [])),
        h.get("color", "#ffd54f"),
        h.get("category", ""),
        h.get("text", ""),
        h.get("note", ""),
        h.get("kind", "user"),
        _now(),
    )
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO highlights
               (id,paper_id,page,rects,color,category,text,note,kind,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            row,
        )
    return get_highlight(hid)


def get_highlight(hid: str) -> dict | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM highlights WHERE id=?", (hid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["rects"] = json.loads(d.get("rects") or "[]")
    return d


def list_highlights(paper_id: str, kind: str | None = None) -> list[dict]:
    q = "SELECT * FROM highlights WHERE paper_id=?"
    args: list[Any] = [paper_id]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY page, created_at"
    with _conn() as con:
        rows = con.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["rects"] = json.loads(d.get("rects") or "[]")
        out.append(d)
    return out


def update_highlight(hid: str, fields: dict) -> dict | None:
    allowed = {"color", "note", "category", "text"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(hid)
        with _conn() as con:
            con.execute(f"UPDATE highlights SET {','.join(sets)} WHERE id=?", vals)
    return get_highlight(hid)


def delete_highlight(hid: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM highlights WHERE id=?", (hid,))


def clear_auto_highlights(paper_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM highlights WHERE paper_id=? AND kind='auto'", (paper_id,))


# ---------------------------------------------------------------------------
# Freehand drawings + personal paper notes
# ---------------------------------------------------------------------------
def add_drawing(paper_id: str, drawing: dict) -> dict:
    did = drawing.get("id") or _uid()
    row = (
        did,
        paper_id,
        int(drawing.get("page", 0)),
        json.dumps(drawing.get("points", [])),
        drawing.get("color", "#ef6b6b"),
        float(drawing.get("width", 3)),
        drawing.get("tool", "pen"),
        drawing.get("note", ""),
        _now(),
    )
    with _conn() as con:
        con.execute(
            """INSERT INTO drawings
               (id,paper_id,page,points,color,width,tool,note,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            row,
        )
    return get_drawing(did)


def get_drawing(did: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM drawings WHERE id=?", (did,)).fetchone()
    if not row:
        return None
    drawing = dict(row)
    drawing["points"] = json.loads(drawing.get("points") or "[]")
    drawing["tool"] = drawing.get("tool") or "pen"
    return drawing


def list_drawings(paper_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM drawings WHERE paper_id=? ORDER BY page, created_at",
            (paper_id,),
        ).fetchall()
    drawings = []
    for row in rows:
        drawing = dict(row)
        drawing["points"] = json.loads(drawing.get("points") or "[]")
        drawing["tool"] = drawing.get("tool") or "pen"
        drawings.append(drawing)
    return drawings


def delete_drawing(did: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM drawings WHERE id=?", (did,))


def get_personal_note(paper_id: str) -> dict:
    with _conn() as con:
        row = con.execute(
            "SELECT paper_id,content,updated_at FROM personal_notes WHERE paper_id=?",
            (paper_id,),
        ).fetchone()
    if row:
        return dict(row)
    return {"paper_id": paper_id, "content": "", "updated_at": None}


def save_personal_note(paper_id: str, content: str) -> dict:
    updated_at = _now()
    with _conn() as con:
        con.execute(
            """INSERT INTO personal_notes (paper_id,content,updated_at)
               VALUES (?,?,?)
               ON CONFLICT(paper_id) DO UPDATE SET
                 content=excluded.content,
                 updated_at=excluded.updated_at""",
            (paper_id, content, updated_at),
        )
    return get_personal_note(paper_id)


# ---------------------------------------------------------------------------
# Chats / messages
# ---------------------------------------------------------------------------
def create_chat(paper_id: str, title: str = "Chat") -> dict:
    cid = _uid()
    created_at = _now()
    with _conn() as con:
        con.execute(
            "INSERT INTO chats (id,paper_id,title,created_at) VALUES (?,?,?,?)",
            (cid, paper_id, title, created_at),
        )
    return {
        "id": cid,
        "paper_id": paper_id,
        "title": title,
        "created_at": created_at,
    }


def list_chats(paper_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id,paper_id,title,created_at
               FROM chats WHERE paper_id=? ORDER BY created_at DESC""",
            (paper_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_chat(chat_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def update_chat_title(chat_id: str, title: str) -> dict | None:
    normalized = " ".join(title.split())[:120]
    if not normalized:
        return None
    with _conn() as con:
        result = con.execute(
            "UPDATE chats SET title=? WHERE id=?", (normalized, chat_id)
        )
        updated = result.rowcount
    return get_chat(chat_id) if updated else None


def chat_has_messages(chat_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM messages WHERE chat_id=? LIMIT 1", (chat_id,)
        ).fetchone()
    return row is not None


def delete_chat(chat_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        con.execute("DELETE FROM chats WHERE id=?", (chat_id,))


def add_message(
    chat_id: str,
    role: str,
    content: str,
    attachments: list[dict] | None = None,
) -> dict:
    mid = _uid()
    serialized_attachments = json.dumps(attachments or [], ensure_ascii=False)
    with _conn() as con:
        con.execute(
            """INSERT INTO messages
               (id,chat_id,role,content,attachments,created_at)
               VALUES (?,?,?,?,?,?)""",
            (mid, chat_id, role, content, serialized_attachments, _now()),
        )
    return {
        "id": mid,
        "chat_id": chat_id,
        "role": role,
        "content": content,
        "attachments": attachments or [],
    }


def commit_chat_turn(
    chat_id: str,
    user_content: str,
    assistant_content: str,
    *,
    provider: str,
    provider_session_id: str | None,
    title: str | None = None,
    user_attachments: list[dict] | None = None,
) -> None:
    """Atomically persist a completed turn and its provider-session mapping."""
    now = _now()
    with _conn() as con:
        con.executemany(
            """INSERT INTO messages
               (id,chat_id,role,content,attachments,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                (
                    _uid(), chat_id, "user", user_content,
                    json.dumps(user_attachments or [], ensure_ascii=False), now,
                ),
                (_uid(), chat_id, "assistant", assistant_content, "[]", now + 0.000001),
            ),
        )
        if title:
            con.execute(
                """UPDATE chats
                   SET provider=?, provider_session_id=?,
                       title=CASE WHEN title='Chat' THEN ? ELSE title END
                   WHERE id=?""",
                (provider, provider_session_id, title, chat_id),
            )
        else:
            con.execute(
                "UPDATE chats SET provider=?, provider_session_id=? WHERE id=?",
                (provider, provider_session_id, chat_id),
            )


def list_messages(chat_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (chat_id,)
        ).fetchall()
    messages = []
    for row in rows:
        message = dict(row)
        try:
            message["attachments"] = json.loads(message.get("attachments") or "[]")
        except json.JSONDecodeError:
            message["attachments"] = []
        messages.append(message)
    return messages


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
def set_refs(paper_id: str, refs: list[dict]) -> None:
    with _conn() as con:
        con.execute("DELETE FROM refs WHERE paper_id=?", (paper_id,))
        for i, r in enumerate(refs):
            con.execute(
                """INSERT INTO refs
                   (id,paper_id,idx,raw,title,authors,year,doi,arxiv_id,url,abstract,resolved)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _uid(), paper_id, r.get("idx", i), r.get("raw", ""),
                    r.get("title", ""), json.dumps(r.get("authors", []), ensure_ascii=False),
                    str(r.get("year", "") or ""), r.get("doi", ""), r.get("arxiv_id", ""),
                    r.get("url", ""), r.get("abstract", ""), int(bool(r.get("resolved", 0))),
                ),
            )


def get_refs(paper_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM refs WHERE paper_id=? ORDER BY idx", (paper_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["authors"] = json.loads(d.get("authors") or "[]")
        out.append(d)
    return out


def update_ref(ref_id: str, fields: dict) -> None:
    allowed = {"title", "authors", "year", "doi", "arxiv_id", "url", "abstract", "resolved"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "authors":
            v = json.dumps(v, ensure_ascii=False)
        if k == "resolved":
            v = int(bool(v))
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(ref_id)
    with _conn() as con:
        con.execute(f"UPDATE refs SET {','.join(sets)} WHERE id=?", vals)


# ---------------------------------------------------------------------------
# Feature cache (summaries, translations, etc.)
# ---------------------------------------------------------------------------
def cache_get(paper_id: str, key: str) -> Any | None:
    with _conn() as con:
        r = con.execute(
            "SELECT value FROM cache WHERE paper_id=? AND key=?", (paper_id, key)
        ).fetchone()
    return json.loads(r["value"]) if r else None


def cache_set(paper_id: str, key: str, value: Any) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO cache (paper_id,key,value,updated_at) VALUES (?,?,?,?)",
            (paper_id, key, json.dumps(value, ensure_ascii=False), _now()),
        )


def cache_delete_prefix(prefix: str) -> int:
    """Delete every cached result whose key starts with an exact prefix."""
    with _conn() as con:
        cursor = con.execute(
            "DELETE FROM cache WHERE substr(key, 1, ?) = ?",
            (len(prefix), prefix),
        )
    return cursor.rowcount
