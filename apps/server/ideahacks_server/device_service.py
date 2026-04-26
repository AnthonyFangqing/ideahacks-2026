import os
from pathlib import Path
import subprocess
import threading
from tempfile import NamedTemporaryFile
import traceback
from typing import Any

from ideahacks_server.config import ServerConfig
from ideahacks_server.errors import ConflictError, ServiceError
from ideahacks_server.events import EventBus


class DeviceService:
    def __init__(
        self, config: ServerConfig, events: EventBus, *, library_uuid: str | None = None
    ):
        self._config = config
        self._events = events
        self._library_uuid = library_uuid
        self._lock = threading.RLock()
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected = False
        self._device_info: dict[str, Any] | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._monitor_thread is not None:
                return
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="ideahacks-device-monitor",
                daemon=True,
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "device": self._device_info if self._connected else None,
                "lastError": self._last_error,
            }

    def list_books(self) -> list[dict[str, Any]]:
        with self._lock:
            self._require_connected_locked()
            return self._list_books_cli()

    def collections(self) -> list[dict[str, Any]]:
        books = self.list_books()
        counts: dict[str, int] = {}
        for book in books:
            for collection in book["collections"] or book["tags"]:
                counts[collection] = counts.get(collection, 0) + 1
        return [
            {"id": name, "name": name, "bookCount": count}
            for name, count in sorted(counts.items(), key=lambda item: item[0].lower())
        ]

    def preferred_formats(self) -> tuple[str, ...]:
        self._require_connected()
        return ("EPUB", "AZW3", "MOBI", "PDF", "TXT")

    def upload_books(
        self,
        files: list[Path],
        names: list[str],
        metadata: list[Any],
        *,
        storage: str = "main",
    ) -> list[dict[str, Any]]:
        if storage not in {"main", "carda", "cardb"}:
            raise ServiceError(f"Unknown device storage: {storage}")
        del metadata
        with self._lock:
            self._require_connected_locked()
            for path, name in zip(files, names, strict=True):
                destination = self._device_destination(name, storage)
                self._run_ebook_device("cp", str(path), destination)
            books = self._list_books_cli()
        self._events.publish("device.updated", {"action": "books.uploaded"})
        return books

    def download_books(self, paths: list[str]) -> list[Path]:
        with self._lock:
            self._require_connected_locked()
            downloaded: list[Path] = []
            for path in paths:
                with NamedTemporaryFile(
                    delete=False,
                    suffix=Path(path).suffix,
                    prefix="ideahacks-device-",
                ) as tmp:
                    temp_path = Path(tmp.name)
                self._run_ebook_device("cp", f"dev:{path}", str(temp_path))
                downloaded.append(temp_path)
            return downloaded

    def delete_books(self, paths: list[str]) -> None:
        with self._lock:
            self._require_connected_locked()
            for path in paths:
                self._run_ebook_device("rm", path)
        self._events.publish(
            "device.updated", {"action": "books.deleted", "paths": paths}
        )

    def eject(self) -> None:
        with self._lock:
            self._require_connected_locked()
            self._run_ebook_device("eject")
            self._set_connected_locked(False, None)

    def detect_once(self) -> None:
        result = subprocess.run(
            ["ebook-device", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = f"{result.stdout}\n{result.stderr}"
        connected = (
            result.returncode == 0
            and "Unable to find a connected ebook reader" not in output
        )
        with self._lock:
            if connected:
                self._last_error = None
                self._set_connected_locked(True, self._parse_info(result.stdout))
            else:
                self._set_connected_locked(False, None)

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.detect_once()
            except Exception as exc:
                with self._lock:
                    self._last_error = "".join(
                        traceback.format_exception_only(type(exc), exc)
                    ).strip()
            self._stop_event.wait(self._config.device_poll_seconds)

    def _set_connected_locked(
        self, connected: bool, info: dict[str, Any] | None
    ) -> None:
        if connected == self._connected:
            self._device_info = info
            return
        self._connected = connected
        self._device_info = info
        self._events.publish(
            "device.connected" if connected else "device.disconnected",
            {"device": info} if connected and info else {},
        )

    def _require_connected(self) -> None:
        with self._lock:
            self._require_connected_locked()

    def _require_connected_locked(self) -> None:
        if not self._connected:
            raise ConflictError("No e-reader is connected")

    def _run_ebook_device(self, *args: str) -> str:
        result = subprocess.run(
            ["ebook-device", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise ServiceError(
                result.stderr.strip() or result.stdout.strip() or "ebook-device failed"
            )
        return result.stdout

    def _list_books_cli(self) -> list[dict[str, Any]]:
        output = self._run_ebook_device("ls", "-R", "/")
        books: list[dict[str, Any]] = []
        current_dir = "/"
        extensions = (".epub", ".azw", ".azw3", ".mobi", ".pdf", ".txt", ".kepub")
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.endswith(":"):
                current_dir = line[:-1] or "/"
                continue
            if line.endswith("/") or not line.lower().endswith(extensions):
                continue
            path = os.path.join(current_dir, line).replace("\\", "/")
            if not path.startswith("/"):
                path = "/" + path
            books.append(
                {
                    "id": f"main:{path}",
                    "storage": "main",
                    "path": path,
                    "lpath": path.removeprefix("/"),
                    "title": Path(path).stem,
                    "authors": [],
                    "tags": [],
                    "collections": [],
                    "series": None,
                    "seriesIndex": None,
                    "size": None,
                    "mime": None,
                    "applicationId": None,
                    "libraryBookId": None,
                }
            )
        return books

    @staticmethod
    def _device_destination(name: str, storage: str) -> str:
        if storage == "carda":
            return f"dev:carda:/{name}"
        if storage == "cardb":
            return f"dev:cardb:/{name}"
        return f"dev:/{name}"

    @staticmethod
    def _parse_info(output: str) -> dict[str, Any]:
        info: dict[str, Any] = {
            "driver": "ebook-device",
            "name": "Calibre ebook-device",
            "formats": ["EPUB", "AZW3", "MOBI", "PDF", "TXT"],
        }
        for line in output.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                normalized = key.strip().lower().replace(" ", "_")
                info[normalized] = value.strip()
                if normalized == "device_name":
                    info["name"] = value.strip()
        return info
