from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import traceback
from typing import Any
from uuid import uuid4

from ideahacks_server.device_service import DeviceService
from ideahacks_server.errors import NotFoundError
from ideahacks_server.events import EventBus
from ideahacks_server.library_service import LibraryService


@dataclass
class TransferJob:
    id: str
    direction: str
    status: str
    total: int
    completed: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "direction": self.direction,
            "status": self.status,
            "total": self.total,
            "completed": self.completed,
            "error": self.error,
            "result": self.result,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class TransferService:
    def __init__(
        self, library: LibraryService, device: DeviceService, events: EventBus
    ):
        self._library = library
        self._device = device
        self._events = events
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ideahacks-transfer"
        )
        self._jobs: dict[str, TransferJob] = {}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def get_job(self, job_id: str) -> dict[str, Any]:
        try:
            return self._jobs[job_id].to_dict()
        except KeyError as exc:
            raise NotFoundError(f"Transfer job {job_id} was not found") from exc

    def library_to_device(
        self, book_ids: list[int], *, storage: str = "main"
    ) -> dict[str, Any]:
        job = self._create_job("library-to-device", len(book_ids))
        self._executor.submit(self._run_library_to_device, job.id, book_ids, storage)
        return job.to_dict()

    def device_to_library(
        self,
        paths: list[str],
        *,
        delete_from_device_after_copy: bool = False,
    ) -> dict[str, Any]:
        job = self._create_job("device-to-library", len(paths))
        self._executor.submit(
            self._run_device_to_library,
            job.id,
            paths,
            delete_from_device_after_copy,
        )
        return job.to_dict()

    def _run_library_to_device(
        self, job_id: str, book_ids: list[int], storage: str
    ) -> None:
        temp_files: list[Path] = []
        try:
            self._mark_started(job_id)
            files: list[Path] = []
            names: list[str] = []
            metadata: list[Any] = []
            preferred = self._device.preferred_formats()
            for book_id in book_ids:
                path, name, book_metadata = self._library.export_book_format(
                    book_id, preferred
                )
                temp_files.append(path)
                files.append(path)
                names.append(name)
                metadata.append(book_metadata)
                self._mark_progress(job_id, len(files) - 1)

            books = self._device.upload_books(files, names, metadata, storage=storage)
            self._mark_completed(job_id, {"deviceBooks": books})
        except Exception as exc:
            self._mark_failed(job_id, exc)
        finally:
            for path in temp_files:
                path.unlink(missing_ok=True)

    def _run_device_to_library(
        self,
        job_id: str,
        paths: list[str],
        delete_from_device_after_copy: bool,
    ) -> None:
        downloaded: list[Path] = []
        try:
            self._mark_started(job_id)
            downloaded = self._device.download_books(paths)
            books = []
            for index, path in enumerate(downloaded, start=1):
                books.append(self._library.import_downloaded_device_file(path))
                self._mark_progress(job_id, index)
            if delete_from_device_after_copy:
                self._device.delete_books(paths)
            self._mark_completed(job_id, {"libraryBooks": books})
        except Exception as exc:
            self._mark_failed(job_id, exc)
        finally:
            for path in downloaded:
                path.unlink(missing_ok=True)

    def _create_job(self, direction: str, total: int) -> TransferJob:
        job = TransferJob(
            id=uuid4().hex, direction=direction, status="queued", total=total
        )
        self._jobs[job.id] = job
        self._events.publish("transfer.queued", {"job": job.to_dict()})
        return job

    def _mark_started(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        job.updated_at = datetime.now(UTC).isoformat()
        self._events.publish("transfer.started", {"job": job.to_dict()})

    def _mark_progress(self, job_id: str, completed: int) -> None:
        job = self._jobs[job_id]
        job.completed = completed
        job.updated_at = datetime.now(UTC).isoformat()
        self._events.publish("transfer.progress", {"job": job.to_dict()})

    def _mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        job = self._jobs[job_id]
        job.status = "completed"
        job.completed = job.total
        job.result = result
        job.updated_at = datetime.now(UTC).isoformat()
        self._events.publish("transfer.completed", {"job": job.to_dict()})

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        job = self._jobs[job_id]
        job.status = "failed"
        job.error = str(exc)
        job.updated_at = datetime.now(UTC).isoformat()
        traceback.print_exc()
        self._events.publish("transfer.failed", {"job": job.to_dict()})
