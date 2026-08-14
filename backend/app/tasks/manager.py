"""Unbounded-concurrency AI jobs with observable lifecycle snapshots."""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .. import config
from ..features import mindmap as mindmap_feat
from ..features import notes as notes_feat
from ..features import reading_lens
from ..features import summarize as summarize_feat
from ..library import store
from ..plugins import manager as plugin_manager
from ..providers import registry

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SUPPORTED_CORE_FEATURES = {"core.summary", "core.notes", "core.mindmap"}

DEFAULT_MESSAGES = {
    "preparing": "Preparing the research task…",
    "reading": "Reading the paper…",
    "searching": "Searching scholarly sources…",
    "thinking": "Analyzing the evidence…",
    "writing": "Writing the result…",
    "saving": "Saving the result…",
    "default": "Researching…",
}
DEFAULT_MESSAGES_ZH = {
    "preparing": "正在准备研究任务…",
    "reading": "正在阅读论文…",
    "searching": "正在联网检索学术来源…",
    "thinking": "正在分析证据…",
    "writing": "正在撰写结果…",
    "saving": "正在保存结果…",
    "default": "研究中…",
}
KNOWN_DEFAULT_TRANSLATIONS = {
    **{value: DEFAULT_MESSAGES_ZH[key] for key, value in DEFAULT_MESSAGES.items()},
    "Preparing the paper": DEFAULT_MESSAGES_ZH["preparing"],
    "Reading the paper": DEFAULT_MESSAGES_ZH["reading"],
    "Searching scholarly sources": DEFAULT_MESSAGES_ZH["searching"],
    "Searching Semantic Scholar, OpenAlex, and citation links": DEFAULT_MESSAGES_ZH["searching"],
    "Analyzing the evidence": DEFAULT_MESSAGES_ZH["thinking"],
    "Writing the result": DEFAULT_MESSAGES_ZH["writing"],
    "Saving the result": DEFAULT_MESSAGES_ZH["saving"],
    "Researching the paper": DEFAULT_MESSAGES_ZH["default"],
}

CORE_AGENTS = {
    "core.summary": {
        "name": "Summary Agent",
        "name_zh": "总结助手",
        "icon": "📝",
        "messages": {
            **DEFAULT_MESSAGES,
            "thinking": "Distilling the paper's main contribution…",
            "writing": "Writing the structured summary…",
        },
        "messages_zh": {
            **DEFAULT_MESSAGES_ZH,
            "thinking": "正在提炼论文的核心贡献…",
            "writing": "正在撰写结构化摘要…",
        },
    },
    "core.notes": {
        "name": "Deep Paper Note Agent",
        "name_zh": "深度论文笔记助手",
        "icon": "📚",
        "messages": {
            **DEFAULT_MESSAGES,
            "thinking": "Connecting methods, evidence, and limitations…",
            "writing": "Writing the deep paper note…",
        },
        "messages_zh": {
            **DEFAULT_MESSAGES_ZH,
            "thinking": "正在连接方法、证据与局限…",
            "writing": "正在撰写深度论文笔记…",
        },
    },
    "core.mindmap": {
        "name": "Concept Map Agent",
        "name_zh": "概念图助手",
        "icon": "🧠",
        "messages": {
            **DEFAULT_MESSAGES,
            "thinking": "Organizing the paper's concepts and relationships…",
            "writing": "Building the concept map…",
        },
        "messages_zh": {
            **DEFAULT_MESSAGES_ZH,
            "thinking": "正在组织论文概念与关系…",
            "writing": "正在构建概念图…",
        },
    },
}


class TaskValidationError(ValueError):
    pass


class TaskNotFoundError(KeyError):
    pass


def _now() -> float:
    return time.time()


def _task_id() -> str:
    return uuid.uuid4().hex


def _agent_for(feature_id: str, language: str | None) -> dict[str, Any]:
    if feature_id in CORE_AGENTS:
        source = CORE_AGENTS[feature_id]
    else:
        plugin_id = feature_id.removeprefix("plugin:")
        manifest = plugin_manager.get_installed(plugin_id) or {}
        source = manifest.get("agent") or {}
    messages = dict(DEFAULT_MESSAGES)
    messages_zh = dict(DEFAULT_MESSAGES_ZH)
    custom_messages = source.get("messages")
    if isinstance(custom_messages, dict):
        for key, value in custom_messages.items():
            if value is None:
                continue
            rendered = str(value)
            messages[str(key)] = rendered
            messages_zh[str(key)] = KNOWN_DEFAULT_TRANSLATIONS.get(rendered, rendered)
    custom_messages_zh = source.get("messages_zh")
    if isinstance(custom_messages_zh, dict):
        messages_zh.update(
            {
                str(key): str(value)
                for key, value in custom_messages_zh.items()
                if value is not None
            }
        )
    return {
        "name": source.get("name") or "Research Assistant",
        "name_zh": source.get("name_zh") or "研究助手",
        "icon": source.get("icon") or "🔬",
        "messages": {
            stage: {"en": message, "zh": messages_zh.get(stage, message)}
            for stage, message in messages.items()
        },
    }


@dataclass
class TaskRecord:
    id: str
    feature_id: str
    paper_id: str | None
    plugin_id: str | None
    refresh: bool
    language: str | None
    input: dict[str, Any]
    agent: dict[str, Any]
    status: str = "queued"
    progress: int = 0
    stage: str = "queued"
    detail: str = "Waiting to start…"
    created_at: float = field(default_factory=_now)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    version: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)
    runner: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "feature_id": self.feature_id,
            "paper_id": self.paper_id,
            "plugin_id": self.plugin_id,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "detail": self.detail,
            "agent": self.agent,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "cancellable": self.status in {"queued", "running"},
        }


class AITaskManager:
    """Starts every submitted job immediately; only shared sessions serialize."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._closed = False

    def validate(self, feature_id: str, paper_id: str | None) -> str | None:
        if feature_id in SUPPORTED_CORE_FEATURES:
            plugin_id = None
        elif feature_id.startswith("plugin:") and feature_id != "plugin:":
            plugin_id = feature_id.removeprefix("plugin:")
            if not plugin_manager.get_installed(plugin_id):
                raise TaskValidationError("plugin is not installed")
        else:
            raise TaskValidationError(f"unsupported feature_id: {feature_id}")
        if not paper_id:
            raise TaskValidationError("paper_id is required for this feature")
        if not store.get_paper(paper_id):
            raise TaskValidationError("paper not found")
        return plugin_id

    async def submit(
        self,
        *,
        feature_id: str,
        paper_id: str | None,
        refresh: bool = False,
        language: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("task manager is shutting down")
        plugin_id = self.validate(feature_id, paper_id)
        record = TaskRecord(
            id=_task_id(),
            feature_id=feature_id,
            paper_id=paper_id,
            plugin_id=plugin_id,
            refresh=refresh,
            language=language,
            input=dict(input or {}),
            agent=_agent_for(feature_id, language),
        )
        self._tasks[record.id] = record
        record.runner = asyncio.create_task(
            self._execute(record), name=f"gloss-ai-{record.id}"
        )
        return record.snapshot()

    def get(self, task_id: str) -> dict[str, Any]:
        try:
            return self._tasks[task_id].snapshot()
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error

    def cached_result(
        self,
        *,
        feature_id: str,
        paper_id: str | None,
        language: str | None = None,
    ) -> Any | None:
        """Read a completed local result without starting an AI task."""
        plugin_id = self.validate(feature_id, paper_id)
        lang = language or config.output_language()
        if feature_id == "core.summary":
            return store.cache_get(
                paper_id,
                reading_lens.cache_key_for_paper(paper_id, f"summary:{lang}"),
            )
        if feature_id == "core.notes":
            cached = store.cache_get(
                paper_id,
                reading_lens.cache_key_for_paper(paper_id, f"notes:{lang}"),
            )
            return {"markdown": cached} if cached is not None else None
        if feature_id == "core.mindmap":
            cached = store.cache_get(
                paper_id,
                reading_lens.cache_key_for_paper(paper_id, f"mindmap2:{lang}"),
            )
            return {"tree": cached} if cached is not None else None
        settings = config.resolve_feature_settings(feature_id)
        return plugin_manager.get_cached_result(
            paper_id,
            plugin_id,
            language=lang,
            configuration=settings.get("configuration") or {},
        )

    async def cancel_plugin_tasks(self, plugin_id: str) -> int:
        """Cancel active work before uninstalling a plugin and its cache."""
        records = [
            record
            for record in self._tasks.values()
            if record.plugin_id == plugin_id
            and record.status not in TERMINAL_STATUSES
            and record.status != "cancelling"
        ]
        if records:
            await asyncio.gather(
                *(self.cancel(record.id) for record in records),
                return_exceptions=False,
            )
        return len(records)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        try:
            record = self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error
        if record.status in TERMINAL_STATUSES or record.status == "cancelling":
            return record.snapshot()
        await self._update(
            record,
            status="cancelling",
            stage="cancelling",
            detail="Stopping the research agent and its provider process…",
        )
        if record.runner:
            record.runner.cancel()
            await asyncio.sleep(0)
        # A task cancelled before its coroutine's first instruction never
        # enters `_execute`, so finalize it here instead of leaving a permanent
        # "cancelling" snapshot.
        if (
            record.status == "cancelling"
            and (record.runner is None or record.runner.done())
        ):
            await self._update(
                record,
                status="cancelled",
                stage="cancelled",
                detail="Research cancelled.",
                finished_at=_now(),
            )
        return record.snapshot()

    async def events(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        try:
            record = self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error
        delivered = -1
        while True:
            if record.version != delivered:
                delivered = record.version
                snapshot = record.snapshot()
                yield snapshot
                if snapshot["status"] in TERMINAL_STATUSES:
                    return
            async with record.condition:
                await record.condition.wait_for(lambda: record.version != delivered)

    async def shutdown(self) -> None:
        self._closed = True
        runners = [
            record.runner
            for record in self._tasks.values()
            if record.runner and not record.runner.done()
        ]
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    async def _update(self, record: TaskRecord, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(record, key, value)
        record.progress = max(0, min(100, int(record.progress)))
        record.version += 1
        async with record.condition:
            record.condition.notify_all()

    def _message(self, record: TaskRecord, stage: str) -> str:
        messages = record.agent.get("messages", {})
        message = messages.get(stage) or messages.get("default")
        if isinstance(message, dict):
            language = (record.language or config.output_language()).lower()
            preferred = "zh" if "中文" in language or "chinese" in language else "en"
            return str(
                message.get(preferred)
                or message.get("en")
                or message.get("zh")
                or DEFAULT_MESSAGES["default"]
            )
        return str(message or DEFAULT_MESSAGES["default"])

    async def _execute(self, record: TaskRecord) -> None:
        provider_work: asyncio.Task | None = None
        try:
            await self._update(
                record,
                status="running",
                progress=5,
                stage="preparing",
                detail=self._message(record, "preparing"),
                started_at=_now(),
            )
            await asyncio.sleep(0)
            await self._update(
                record,
                progress=15,
                stage="reading",
                detail=self._message(record, "reading"),
            )
            settings = config.resolve_feature_settings(record.feature_id, record.input)
            await self._update(
                record,
                progress=30,
                stage="thinking",
                detail=self._message(record, "thinking"),
            )
            provider_work = asyncio.create_task(self._dispatch(record, settings))
            result = await provider_work
            await self._update(
                record,
                progress=92,
                stage="writing",
                detail=self._message(record, "writing"),
            )
            await asyncio.sleep(0)
            await self._update(
                record,
                progress=97,
                stage="saving",
                detail=self._message(record, "saving"),
            )
            await self._update(
                record,
                status="completed",
                progress=100,
                stage="completed",
                detail="Research complete.",
                result=result,
                error=None,
                finished_at=_now(),
            )
        except asyncio.CancelledError:
            if provider_work and not provider_work.done():
                provider_work.cancel()
                with suppress(BaseException):
                    await provider_work
            await self._update(
                record,
                status="cancelled",
                stage="cancelled",
                detail="Research cancelled.",
                error=None,
                finished_at=_now(),
            )
        except Exception as error:
            await self._update(
                record,
                status="failed",
                stage="failed",
                detail="The research agent could not finish.",
                error=str(error) or type(error).__name__,
                finished_at=_now(),
            )

    async def _dispatch(
        self, record: TaskRecord, settings: dict[str, Any]
    ) -> Any:
        provider = settings.get("provider")
        model = settings.get("model") or None
        effort = settings.get("effort") or None
        context_mode = settings.get("context_mode") or "full"
        paper_id = record.paper_id
        language = record.language or record.input.get("language")
        task_type = record.feature_id.removeprefix("core.")
        with (
            registry.usage_context(
                "plugin" if record.plugin_id else task_type,
                paper_id=paper_id,
                plugin_id=record.plugin_id,
            ),
            registry.feature_context(
                paper_id=paper_id,
                context_mode=context_mode,
                effort=effort,
            ),
        ):
            if record.feature_id == "core.summary":
                return await summarize_feat.summarize_paper(
                    paper_id,
                    language=language,
                    refresh=record.refresh,
                    provider=provider,
                    model=model,
                )
            if record.feature_id == "core.notes":
                markdown = await notes_feat.build_notes(
                    paper_id,
                    language=language,
                    refresh=record.refresh,
                    provider=provider,
                    model=model,
                )
                return {"markdown": markdown}
            if record.feature_id == "core.mindmap":
                tree = await mindmap_feat.build_mindmap(
                    paper_id,
                    language=language,
                    refresh=record.refresh,
                    provider=provider,
                    model=model,
                )
                return {"tree": tree}
            manifest = plugin_manager.get_installed(record.plugin_id) or {}
            if "network:research" in manifest.get("permissions", []):
                await self._update(
                    record,
                    progress=35,
                    stage="searching",
                    detail=self._message(record, "searching"),
                )
            return await plugin_manager.run_plugin(
                paper_id,
                record.plugin_id,
                refresh=record.refresh,
                language=language,
                provider=provider,
                model=model,
                configuration=settings.get("configuration") or {},
            )
