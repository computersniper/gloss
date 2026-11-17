"""In-process asynchronous paper-import queue.

Jobs survive SPA navigation and browser refresh because their state lives in the
backend process. They intentionally do not survive a backend restart.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool

from . import importers, service, store
from ..platform_support import ProcessCancelledError, run_in_process_with_timeout


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _cleanup_pending(job: "ImportJob") -> bool:
    return job.status == "failed" and bool(job.paper_id)


class ImportQueueFullError(RuntimeError):
    pass


class ImportCleanupError(RuntimeError):
    pass


class _JobCancelled(RuntimeError):
    pass


@dataclass
class ImportJob:
    id: str
    query: str
    payload: dict[str, Any]
    status: str = "queued"
    progress: int = 0
    stage_detail: str = "Waiting to import"
    title: str = ""
    paper_id: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    revision: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status,
            "progress": self.progress,
            "stage_detail": self.stage_detail,
            "title": self.title,
            "paper_id": self.paper_id,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
        }


class ImportJobManager:
    """Own queued imports for one local, single-worker Gloss process."""

    def __init__(
        self,
        *,
        download_timeout: float,
        parse_timeout: float,
        save_timeout: float,
        concurrency: int = 1,
        max_active_jobs: int = 20,
        max_terminal_jobs: int = 20,
    ) -> None:
        self.download_timeout = download_timeout
        self.parse_timeout = parse_timeout
        self.save_timeout = save_timeout
        self.max_active_jobs = max_active_jobs
        self.max_terminal_jobs = max_terminal_jobs
        self._semaphore = asyncio.Semaphore(concurrency)
        self._jobs: dict[str, ImportJob] = {}
        self._lock = asyncio.Lock()
        self._accepting = True

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(
            payload.get("query")
            or payload.get("arxiv")
            or payload.get("doi")
            or payload.get("url")
            or ""
        ).strip()
        if not query:
            raise ValueError("empty import query")

        async with self._lock:
            if not self._accepting:
                raise RuntimeError("import queue is shutting down")
            active = sum(
                job.status not in TERMINAL_STATUSES or _cleanup_pending(job)
                for job in self._jobs.values()
            )
            if active >= self.max_active_jobs:
                raise ImportQueueFullError("import queue is full")
            self._prune_terminal_locked()
            job = ImportJob(
                id=uuid.uuid4().hex,
                query=query,
                payload=dict(payload),
            )
            self._jobs[job.id] = job
            job.task = asyncio.create_task(
                self._run(job), name=f"gloss-import-{job.id[:8]}"
            )
        return job.snapshot()

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at)
            return [job.snapshot() for job in jobs]

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    async def cancel_or_clear(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False

        should_cancel_task = False
        async with job.lock:
            if job.status in TERMINAL_STATUSES:
                terminal = True
            else:
                terminal = False
                previous_status = job.status
                job.cancel_event.set()
                self._mutate(job, status="cancelling", stage_detail="Cancelling import")
                should_cancel_task = previous_status in {"queued", "downloading"}

        if not terminal:
            if should_cancel_task and job.task is not None:
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)
                job.done.set()
            else:
                await job.done.wait()

        if job.task is not None:
            task_result = await asyncio.gather(job.task, return_exceptions=True)
            unexpected = task_result[0] if task_result else None
            if isinstance(unexpected, BaseException) and not isinstance(
                unexpected, asyncio.CancelledError
            ):
                async with job.lock:
                    self._mutate(
                        job,
                        status="failed",
                        stage_detail="Import failed",
                        error=f"Background task failed: {unexpected}",
                    )

        # A failed cancellation cleanup retains the target paper id. Retry it
        # here, and never erase the only recovery handle when it still fails.
        if job.status == "failed" and job.paper_id:
            try:
                await self._cleanup_paper(job)
            except Exception as error:
                async with job.lock:
                    self._mutate(
                        job,
                        error=f"Import cleanup failed: {type(error).__name__}: {error}",
                    )
                raise ImportCleanupError(job.error) from error

        async with self._lock:
            if self._jobs.get(job_id) is job:
                del self._jobs[job_id]
        return True

    async def shutdown(self) -> None:
        async with self._lock:
            self._accepting = False
            job_ids = list(self._jobs)
        await asyncio.gather(
            *(self.cancel_or_clear(job_id) for job_id in job_ids),
            return_exceptions=True,
        )

    async def _begin_stage(
        self, job: ImportJob, status: str, progress: int, stage_detail: str
    ) -> None:
        async with job.lock:
            if job.cancel_event.is_set():
                raise _JobCancelled()
            self._mutate(
                job,
                status=status,
                progress=max(job.progress, progress),
                stage_detail=stage_detail,
            )

    def _mutate(self, job: ImportJob, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        job.revision += 1

    async def _mark_cancelled(self, job: ImportJob) -> None:
        async with job.lock:
            self._mutate(
                job,
                status="cancelled",
                stage_detail="Import cancelled",
                error=None,
            )

    async def _cleanup_paper(self, job: ImportJob) -> None:
        if not job.paper_id:
            return
        paper_id = job.paper_id
        await run_in_threadpool(store.delete_paper, paper_id)
        job.paper_id = None

    async def _finish_cancelled(self, job: ImportJob) -> None:
        try:
            await self._cleanup_paper(job)
        except Exception as error:
            async with job.lock:
                self._mutate(
                    job,
                    status="failed",
                    stage_detail="Cleanup failed",
                    error=f"Import cleanup failed: {type(error).__name__}: {error}",
                )
            return
        await self._mark_cancelled(job)

    async def _finish_failed(self, job: ImportJob, error: BaseException) -> None:
        cleanup_error: BaseException | None = None
        try:
            await self._cleanup_paper(job)
        except Exception as caught:
            cleanup_error = caught
        message = f"{type(error).__name__}: {error}"
        if cleanup_error is not None:
            message += (
                f"; cleanup failed: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        async with job.lock:
            self._mutate(
                job,
                status="failed",
                stage_detail="Cleanup failed" if cleanup_error else "Import failed",
                error=message,
            )

    async def _persist(
        self,
        job: ImportJob,
        pdf: bytes,
        meta: dict[str, Any],
        parsed: dict[str, Any],
    ) -> dict[str, Any]:
        # Importer metadata is untrusted. Never adopt its id: on an INSERT
        # conflict, compensating cleanup could otherwise delete an older paper.
        paper_id = uuid.uuid4().hex
        job.paper_id = paper_id
        persisted_meta = {**meta, "id": paper_id}
        return await run_in_threadpool(
            run_in_process_with_timeout,
            service.create_from_pdf_bytes,
            (pdf, persisted_meta, parsed),
            self.save_timeout,
            job.cancel_event,
        )

    async def _run(self, job: ImportJob) -> None:
        try:
            async with self._semaphore:
                await self._begin_stage(job, "downloading", 10, "Downloading paper")
                meta, pdf = await asyncio.wait_for(
                    importers.import_source(job.payload),
                    timeout=self.download_timeout,
                )
                job.title = str(meta.get("title") or "")

                await self._begin_stage(job, "parsing", 45, "Parsing PDF")
                parsed = await run_in_threadpool(
                    service.parse_pdf_bytes_with_timeout,
                    pdf,
                    self.parse_timeout,
                    job.cancel_event,
                )

                await self._begin_stage(job, "saving", 85, "Saving to library")
                paper = await self._persist(job, pdf, meta, parsed)
                if not job.title:
                    job.title = str(paper.get("title") or "")

                cancel_won = False
                async with job.lock:
                    if job.cancel_event.is_set():
                        cancel_won = True
                    else:
                        self._mutate(
                            job,
                            status="completed",
                            progress=100,
                            stage_detail="Import complete",
                        )
                if cancel_won:
                    await self._finish_cancelled(job)
                else:
                    from ..routers.papers import _seed_paper_project
                    await run_in_threadpool(_seed_paper_project, paper["id"])
        except asyncio.CancelledError:
            await self._finish_cancelled(job)
        except (_JobCancelled, ProcessCancelledError, service.ImportCancelledError):
            await self._finish_cancelled(job)
        except TimeoutError as error:
            if job.cancel_event.is_set():
                await self._finish_cancelled(job)
            else:
                await self._finish_failed(job, error)
        except Exception as error:  # noqa: BLE001 - job errors surface in the UI
            if job.cancel_event.is_set():
                await self._finish_cancelled(job)
            else:
                await self._finish_failed(job, error)
        finally:
            job.done.set()

    def _prune_terminal_locked(self) -> None:
        terminal = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in TERMINAL_STATUSES and not _cleanup_pending(job)
            ),
            key=lambda job: job.updated_at,
        )
        excess = len(terminal) - self.max_terminal_jobs + 1
        for job in terminal[: max(0, excess)]:
            self._jobs.pop(job.id, None)
