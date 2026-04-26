import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Any

from ideahacks_server.config import ServerConfig
from ideahacks_server.errors import NotFoundError, ServiceError
from ideahacks_server.events import EventBus


DEFAULT_FORMAT_PRIORITY = ("EPUB", "AZW3", "MOBI", "PDF", "KEPUB", "TXT")


class LibraryService:
    def __init__(self, config: ServerConfig, events: EventBus):
        self._config = config
        self._events = events
        self._lock = RLock()

    @property
    def library_path(self) -> Path:
        return self._config.library_path

    def close(self) -> None:
        # Calibre CLI processes are short-lived, so there is no persistent handle.
        return None

    def list_books(self, search: str | None = None) -> list[dict[str, Any]]:
        return self._list_books_cli(search)

    def get_book(self, book_id: int) -> dict[str, Any]:
        for book in self._list_books_cli(None):
            if book["id"] == book_id:
                return book
        raise NotFoundError(f"Book {book_id} was not found")

    def get_metadata(self, book_id: int) -> Any:
        return self.get_book(book_id)

    def get_collections(self) -> dict[str, list[dict[str, Any]]]:
        books = self.list_books()
        tags: dict[str, int] = {}
        series: dict[str, int] = {}
        authors: dict[str, int] = {}
        for book in books:
            for tag in book["tags"]:
                tags[tag] = tags.get(tag, 0) + 1
            if book["series"]:
                series[book["series"]] = series.get(book["series"], 0) + 1
            for author in book["authors"]:
                authors[author] = authors.get(author, 0) + 1
        return {
            "tags": self._collection_items(tags),
            "series": self._collection_items(series),
            "authors": self._collection_items(authors),
        }

    def cover_bytes(self, book_id: int) -> bytes:
        return self._cover_bytes_cli(book_id)

    def add_book_from_path(
        self, path: Path, *, delete_source: bool = False
    ) -> dict[str, Any]:
        return self._add_book_cli(path, delete_source=delete_source)

    def update_book(self, book_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        fields = self._metadata_fields_from_patch(patch)
        if not fields:
            return self.get_book(book_id)
        args = ["set_metadata", str(book_id)]
        for field in fields:
            args.extend(["--field", field])
        self._run_calibredb(*args)
        book = self.get_book(book_id)
        self._events.publish(
            "library.updated", {"action": "book.updated", "book": book}
        )
        return book

    def remove_book(self, book_id: int, *, permanent: bool = False) -> None:
        args = ["remove", str(book_id)]
        if permanent:
            args.append("--permanent")
        self._run_calibredb(*args)
        self._events.publish(
            "library.updated", {"action": "book.removed", "bookId": book_id}
        )

    def export_book_format(
        self,
        book_id: int,
        preferred_formats: tuple[str, ...] = DEFAULT_FORMAT_PRIORITY,
    ) -> tuple[Path, str, Any]:
        return self._export_book_format_cli(book_id, preferred_formats)

    def import_downloaded_device_file(self, source: Path) -> dict[str, Any]:
        try:
            return self.add_book_from_path(source, delete_source=True)
        finally:
            source.unlink(missing_ok=True)

    def copy_book_file_to(self, book_id: int, fmt: str, target: Path) -> None:
        with TemporaryDirectory(prefix="ideahacks-export-") as temp_dir:
            self._run_calibredb(
                "export",
                "--to-dir",
                temp_dir,
                "--single-dir",
                "--dont-save-cover",
                "--dont-write-opf",
                "--dont-save-extra-files",
                "--formats",
                fmt.lower(),
                str(book_id),
            )
            exported = next(Path(temp_dir).rglob(f"*.{fmt.lower()}"), None)
            if exported is None:
                raise ServiceError(f"Could not export book {book_id} as {fmt}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(exported, target)

    def _run_calibredb(self, *args: str) -> str:
        self.library_path.mkdir(parents=True, exist_ok=True)
        command = ["calibredb", *args, "--library-path", str(self.library_path)]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise ServiceError(
                result.stderr.strip() or result.stdout.strip() or "calibredb failed"
            )
        return result.stdout

    def _list_books_cli(self, search: str | None) -> list[dict[str, Any]]:
        fields = "id,title,authors,tags,series,series_index,formats,uuid,publisher,languages,identifiers"
        args = ["list", "--for-machine", "--fields", fields]
        if search:
            args.extend(["--search", search])
        raw = self._run_calibredb(*args)
        rows = json.loads(raw or "[]")
        return [self._cli_row_to_book(row) for row in rows]

    @staticmethod
    def _metadata_fields_from_patch(patch: dict[str, Any]) -> list[str]:
        fields: list[str] = []
        field_map = {
            "title": "title",
            "authors": "authors",
            "tags": "tags",
            "series": "series",
            "seriesIndex": "series_index",
            "publisher": "publisher",
            "languages": "languages",
        }
        for source, field_name in field_map.items():
            if source not in patch:
                continue
            value = patch[source]
            if isinstance(value, list):
                value = ",".join(str(item) for item in value)
            fields.append(f"{field_name}:{value}")
        return fields

    def _add_book_cli(self, path: Path, *, delete_source: bool) -> dict[str, Any]:
        output = self._run_calibredb("add", str(path))
        if delete_source:
            path.unlink(missing_ok=True)
        match = re.search(r"Added book ids:\s*([0-9, ]+)", output)
        if not match:
            raise ServiceError(
                output.strip() or "Calibre did not report an added book id"
            )
        book_id = int(match.group(1).split(",")[0].strip())
        book = self.get_book(book_id)
        self._events.publish("library.updated", {"action": "book.added", "book": book})
        return book

    def _cover_bytes_cli(self, book_id: int) -> bytes:
        with TemporaryDirectory(prefix="ideahacks-cover-") as temp_dir:
            self._run_calibredb(
                "export",
                "--to-dir",
                temp_dir,
                "--single-dir",
                "--dont-write-opf",
                str(book_id),
            )
            for candidate in Path(temp_dir).rglob("cover.*"):
                return candidate.read_bytes()
        raise NotFoundError(f"Book {book_id} does not have a cover")

    def _export_book_format_cli(
        self, book_id: int, preferred_formats: tuple[str, ...]
    ) -> tuple[Path, str, Any]:
        book = self.get_book(book_id)
        formats = tuple(book["formats"]) or preferred_formats
        fmt = self._choose_format(tuple(formats), preferred_formats)
        with TemporaryDirectory(prefix="ideahacks-export-") as temp_dir:
            self._run_calibredb(
                "export",
                "--to-dir",
                temp_dir,
                "--single-dir",
                "--dont-save-cover",
                "--dont-write-opf",
                "--dont-save-extra-files",
                "--formats",
                fmt.lower(),
                str(book_id),
            )
            exported = next(Path(temp_dir).rglob(f"*.{fmt.lower()}"), None)
            if exported is None:
                raise ServiceError(f"Could not export book {book_id} as {fmt}")
            with NamedTemporaryFile(
                delete=False, suffix=exported.suffix, prefix="ideahacks-export-"
            ) as tmp:
                shutil.copyfile(exported, tmp.name)
                return Path(tmp.name), exported.name, book

    @staticmethod
    def _cli_row_to_book(row: dict[str, Any]) -> dict[str, Any]:
        book_id = int(row["id"])

        def list_value(name: str) -> list[str]:
            value = row.get(name) or []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            return list(value)

        return {
            "id": book_id,
            "title": row.get("title") or "Unknown",
            "authors": list_value("authors"),
            "tags": list_value("tags"),
            "series": row.get("series"),
            "seriesIndex": row.get("series_index"),
            "publisher": row.get("publisher"),
            "languages": list_value("languages"),
            "identifiers": row.get("identifiers") or {},
            "uuid": row.get("uuid"),
            "formats": [fmt.upper() for fmt in list_value("formats")],
            "lastModified": None,
            "publishedAt": None,
            "coverUrl": f"/api/library/books/{book_id}/cover",
        }

    @staticmethod
    def _choose_format(available: tuple[str, ...], preferred: tuple[str, ...]) -> str:
        if not available:
            raise ServiceError("Book has no readable formats")
        available_set = set(available)
        for fmt in preferred:
            if fmt.upper() in available_set:
                return fmt.upper()
        return available[0]

    @staticmethod
    def _collection_items(values: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"id": name, "name": name, "bookCount": count}
            for name, count in sorted(values.items(), key=lambda item: item[0].lower())
        ]

    @staticmethod
    def save_upload(file_storage: Any) -> Path:
        suffix = Path(file_storage.filename or "").suffix
        with NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="ideahacks-upload-"
        ) as tmp:
            file_storage.save(tmp)
            return Path(tmp.name)

    @staticmethod
    def discard_temp(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
