"""AI feature endpoints: summarize, explain, translate, auto-highlight, chat (SSE)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..features import chat as chat_feat
from ..features import explain as explain_feat
from ..features import highlight as highlight_feat
from ..features import mindmap as mindmap_feat
from ..features import notes as notes_feat
from ..features import summarize as summarize_feat
from ..features import translate as translate_feat
from ..library import store
from ..providers import registry

router = APIRouter(prefix="/api", tags=["ai"])
_CHAT_LOCKS: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def _serialize_chat(chat_id: str | None):
    if not chat_id:
        yield
        return
    lock = _CHAT_LOCKS.setdefault(chat_id, asyncio.Lock())
    async with lock:
        yield


class Override(BaseModel):
    provider: str | None = None
    model: str | None = None
    language: str | None = None


class SummarizeBody(Override):
    refresh: bool = False


class ExplainBody(Override):
    paper_id: str | None = None
    selection: str
    context: str | None = None


class TranslateBody(Override):
    text: str | None = None
    paper_id: str | None = None
    page: int | None = None          # single page (0-based)
    page_start: int | None = None    # page range (0-based, inclusive)
    page_end: int | None = None


class ChatAttachment(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["pdf_region"] = "pdf_region"
    page: int = Field(ge=0)
    image_data_url: str = Field(max_length=8_000_000)
    extracted_text: str = Field(default="", max_length=30_000)
    bounds: tuple[float, float, float, float]

    @field_validator("image_data_url")
    @classmethod
    def validate_image_data_url(cls, value: str) -> str:
        prefix = "data:image/png;base64,"
        if not value.startswith(prefix):
            raise ValueError("chat attachment must be a base64 PNG data URL")
        try:
            base64.b64decode(value[len(prefix):], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("chat attachment contains invalid base64 data") from error
        return value


class ChatMessage(BaseModel):
    role: str
    content: str
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)


class ChatBody(Override):
    paper_id: str | None = None
    chat_id: str | None = None
    messages: list[ChatMessage]
    selection: str | None = None
    # side chat of an agent thread: it reads a recap of that thread, never writes to it
    session_id: str | None = None


@router.post("/papers/{paper_id}/summarize")
async def summarize(paper_id: str, body: SummarizeBody):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    try:
        with registry.usage_context("summary", paper_id=paper_id):
            return await summarize_feat.summarize_paper(
                paper_id, language=body.language, refresh=body.refresh,
                provider=body.provider, model=body.model,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/papers/{paper_id}/mindmap")
async def mindmap(paper_id: str, body: SummarizeBody):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    try:
        with registry.usage_context("mindmap", paper_id=paper_id):
            tree = await mindmap_feat.build_mindmap(
                paper_id, language=body.language, refresh=body.refresh,
                provider=body.provider, model=body.model,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"tree": tree}


@router.post("/papers/{paper_id}/notes")
async def notes(paper_id: str, body: SummarizeBody):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    try:
        with registry.usage_context("notes", paper_id=paper_id):
            md = await notes_feat.build_notes(
                paper_id, language=body.language, refresh=body.refresh,
                provider=body.provider, model=body.model,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"markdown": md}


@router.post("/explain")
async def explain(body: ExplainBody):
    if not body.selection.strip():
        raise HTTPException(400, "empty selection")
    with registry.usage_context("explain", paper_id=body.paper_id):
        text = await explain_feat.explain_selection(
            body.paper_id, body.selection, context=body.context,
            language=body.language, provider=body.provider, model=body.model,
        )
    return {"explanation": text}


@router.post("/translate")
async def translate(body: TranslateBody):
    # page range (from page_start to page_end, 0-based inclusive)
    if body.paper_id and body.page_start is not None:
        end = body.page_end if body.page_end is not None else body.page_start
        try:
            with registry.usage_context("translate", paper_id=body.paper_id):
                sents = await translate_feat.translate_range(
                    body.paper_id, body.page_start, end, language=body.language,
                    provider=body.provider, model=body.model,
                )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"sentences": sents}
    # single page
    if body.paper_id and body.page is not None:
        try:
            with registry.usage_context("translate", paper_id=body.paper_id):
                sents = await translate_feat.translate_page(
                    body.paper_id, body.page, language=body.language,
                    provider=body.provider, model=body.model,
                )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"sentences": sents}
    if body.text:
        with registry.usage_context("translate"):
            out = await translate_feat.translate_text(
                body.text, language=body.language, provider=body.provider, model=body.model
            )
        return {"translation": out}
    raise HTTPException(400, "provide text, or paper_id + page")


@router.get("/papers/{paper_id}/translations")
async def get_translations(paper_id: str, lang: str | None = None):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    sents = translate_feat.get_translations(paper_id, language=lang)
    return {
        "sentences": sents,
        "pages": sorted({s["page"] for s in sents}),
    }


class TexTransBody(Override):
    section: int | None = None  # section index; None = all sections


@router.post("/papers/{paper_id}/translate_tex")
async def translate_tex(paper_id: str, body: TexTransBody):
    """Translate an arXiv paper's LaTeX source, by section (or all)."""
    try:
        with registry.usage_context("translate_tex", paper_id=paper_id):
            sections = await translate_feat.translate_tex(
                paper_id, section=body.section, language=body.language,
                provider=body.provider, model=body.model,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"sections": sections}


@router.get("/papers/{paper_id}/tex_sections")
async def get_tex_sections(paper_id: str, lang: str | None = None):
    return {"sections": await translate_feat.tex_sections(paper_id, language=lang)}


@router.post("/papers/{paper_id}/autohighlight")
async def autohighlight(paper_id: str, body: Override):
    if not store.get_paper(paper_id):
        raise HTTPException(404, "paper not found")
    try:
        with registry.usage_context("auto_highlight", paper_id=paper_id):
            items = await highlight_feat.auto_highlight(
                paper_id, provider=body.provider, model=body.model
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"highlights": items}


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    messages = [m.model_dump() for m in body.messages]
    paper_id = body.paper_id
    main_context = None
    if body.session_id:
        manager = getattr(request.app.state, "sessions", None)
        try:
            main_context = manager.digest(body.session_id) if manager else None
        except Exception:  # noqa: BLE001 - a missing thread only drops the recap
            main_context = None
    if body.chat_id:
        chat_record = store.get_chat(body.chat_id)
        if not chat_record:
            raise HTTPException(404, "chat not found")
        if paper_id and chat_record["paper_id"] != paper_id:
            raise HTTPException(400, "chat does not belong to this paper")
        paper_id = paper_id or chat_record["paper_id"]

    async def gen():
        acc = []
        session_state: dict = {}
        try:
            with registry.usage_context("chat", paper_id=paper_id):
                async with _serialize_chat(body.chat_id):
                    chat_record = store.get_chat(body.chat_id) if body.chat_id else None
                    should_auto_title = bool(
                        chat_record
                        and chat_record.get("title") == "Chat"
                        and not store.chat_has_messages(body.chat_id)
                    )
                    async for delta in chat_feat.chat_stream(
                        paper_id,
                        messages,
                        chat_id=body.chat_id,
                        session_state=session_state,
                        selection=body.selection,
                        main_context=main_context,
                        language=body.language,
                        provider=body.provider,
                        model=body.model,
                    ):
                        acc.append(delta)
                        yield f"data: {json.dumps({'delta': delta})}\n\n"

                    # Persist the transcript and the provider-session pointer in one
                    # transaction, but only after the provider completed cleanly.
                    if body.chat_id and messages:
                        title = None
                        if should_auto_title:
                            title = chat_feat.summarize_chat_title(
                                messages[-1]["content"], "".join(acc)
                            )
                        store.commit_chat_turn(
                            body.chat_id,
                            messages[-1]["content"],
                            "".join(acc),
                            provider=session_state.get("provider")
                            or registry.resolve_provider_name(body.provider),
                            provider_session_id=session_state.get("provider_session_id"),
                            title=title,
                            user_attachments=messages[-1].get("attachments", []),
                        )
        except Exception as e:  # surface errors to the client stream
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
