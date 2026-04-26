from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parent / "data" / "calibre-library"
PREFERRED_SEND_FORMATS = ("EPUB", "AZW3", "MOBI", "PDF")


class CalibreLibraryError(RuntimeError):
    """Raised when a Calibre library operation fails."""


def _find_executable(name: str, macos_app_name: str | None = None) -> str:
    if path := shutil.which(name):
        return path

    candidates = []
    if sys.platform == "darwin" and macos_app_name is not None:
        candidates.append(f"/Applications/calibre.app/Contents/MacOS/{macos_app_name}")
    elif sys.platform.startswith("linux"):
        candidates.extend([
            f"/usr/bin/{name}",
            f"/opt/calibre/{name}",
            f"/usr/local/bin/{name}",
        ])

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    raise CalibreLibraryError(f"{name} not found. Install Calibre or ensure it's on PATH.")


CALIBREDB_EXECUTABLE = _find_executable("calibredb", "calibredb")


def configured_library_path() -> Path:
    configured = os.environ.get("IDEAHACKS_CALIBRE_LIBRARY")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_LIBRARY_PATH


def library_status() -> dict[str, Any]:
    library_path = configured_library_path()
    return {
        "path": str(library_path),
        "exists": library_path.exists(),
        "metadata_db_exists": (library_path / "metadata.db").exists(),
    }


def list_library_books(query: str | None = None) -> list[dict[str, Any]]:
    library_path = configured_library_path()
    if not (library_path / "metadata.db").exists():
        return []

    command = [
        CALIBREDB_EXECUTABLE,
        "list",
        "--library-path",
        str(library_path),
        "--for-machine",
        "--fields",
        ",".join([
            "id",
            "title",
            "authors",
            "author_sort",
            "publisher",
            "pubdate",
            "series",
            "series_index",
            "tags",
            "languages",
            "identifiers",
            "formats",
            "comments",
        ]),
        "--sort-by",
        "title",
        "--ascending",
    ]
    if query:
        command.extend(["--search", query])

    decoded = _run_json_command(command)
    if not isinstance(decoded, list):
        raise CalibreLibraryError("calibredb returned an unexpected book list")

    return [normalize_library_book(book) for book in decoded if isinstance(book, dict)]


def export_library_book(book_id: int, requested_format: str | None = None) -> dict[str, Any]:
    book = get_library_book(book_id)
    selected_format = choose_format(book, requested_format)

    with tempfile.TemporaryDirectory(prefix="ideahacks_calibre_export_") as export_dir:
        command = [
            CALIBREDB_EXECUTABLE,
            "export",
            "--library-path",
            str(configured_library_path()),
            "--to-dir",
            export_dir,
            "--single-dir",
            "--formats",
            selected_format,
            "--dont-save-cover",
            "--dont-write-opf",
            str(book_id),
        ]
        _run_command(command)

        exported_files = [
            path
            for path in Path(export_dir).rglob("*")
            if path.is_file() and path.suffix.lower() == f".{selected_format.lower()}"
        ]
        if not exported_files:
            raise CalibreLibraryError(f"Calibre did not export a {selected_format} file")

        persistent = tempfile.NamedTemporaryFile(
            prefix="ideahacks_library_book_",
            suffix=exported_files[0].suffix,
            delete=False,
        )
        persistent.close()
        shutil.copyfile(exported_files[0], persistent.name)

    return {
        "path": persistent.name,
        "filename": exported_files[0].name,
        "format": selected_format,
        "book": book,
    }


def import_file_to_library(
    file_path: str,
    metadata: dict[str, Any] | None = None,
    delete_after_import: bool = False,
) -> list[int]:
    library_path = configured_library_path()
    library_path.mkdir(parents=True, exist_ok=True)

    command = [
        CALIBREDB_EXECUTABLE,
        "add",
        "--library-path",
        str(library_path),
        file_path,
    ]
    if metadata:
        if title := metadata.get("title"):
            command.extend(["--title", str(title)])
        authors = metadata.get("authors") or metadata.get("authors_display")
        if isinstance(authors, list):
            authors = " & ".join(str(author) for author in authors)
        if authors:
            command.extend(["--authors", str(authors)])
        if tags := metadata.get("tags"):
            if isinstance(tags, list):
                tags = ",".join(str(tag) for tag in tags)
            command.extend(["--tags", str(tags)])
        if series := metadata.get("series"):
            command.extend(["--series", str(series)])
        if series_index := metadata.get("series_index"):
            command.extend(["--series-index", str(series_index)])
        identifiers = metadata.get("identifiers")
        if isinstance(identifiers, dict):
            for key, value in identifiers.items():
                command.extend(["--identifier", f"{key}:{value}"])

    result = _run_command(command)
    if delete_after_import:
        Path(file_path).unlink(missing_ok=True)

    return parse_added_ids(result.stdout)


def get_library_book(book_id: int) -> dict[str, Any]:
    for book in list_library_books(f"id:{book_id}"):
        if book.get("id") == book_id:
            return book
    raise CalibreLibraryError(f"Book {book_id} was not found in the Calibre library")


def choose_format(book: dict[str, Any], requested_format: str | None = None) -> str:
    available = {str(fmt).upper() for fmt in book.get("formats", [])}
    if requested_format:
        normalized = requested_format.upper()
        if normalized not in available:
            raise CalibreLibraryError(f"Book does not have format {normalized}")
        return normalized

    for candidate in PREFERRED_SEND_FORMATS:
        if candidate in available:
            return candidate

    if available:
        return sorted(available)[0]
    raise CalibreLibraryError("Book has no exportable formats")


def normalize_library_book(book: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(book)
    if "id" in normalized:
        normalized["id"] = int(normalized["id"])

    authors = normalized.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    normalized["authors"] = [str(author) for author in authors]
    normalized["authors_display"] = " & ".join(normalized["authors"])

    formats = normalized.get("formats") or []
    if isinstance(formats, str):
        formats = [part.strip() for part in formats.split(",") if part.strip()]
    normalized["formats"] = normalize_formats(formats)

    tags = normalized.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    normalized["tags"] = [str(tag) for tag in tags]

    identifiers = normalized.get("identifiers")
    if not isinstance(identifiers, dict):
        normalized["identifiers"] = {}

    return normalized


def normalize_formats(formats: list[Any]) -> list[str]:
    normalized: list[str] = []
    for raw_format in formats:
        value = str(raw_format).strip()
        if not value:
            continue
        if os.sep in value or "/" in value:
            value = Path(value).suffix.removeprefix(".")
        value = value.upper()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def parse_added_ids(output: str) -> list[int]:
    ids: list[int] = []
    for token in output.replace(",", " ").split():
        if token.isdigit():
            ids.append(int(token))
    return ids


def _run_json_command(command: list[str]) -> Any:
    result = _run_command(command)
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise CalibreLibraryError("calibredb returned invalid JSON") from exc


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    started_at = time.perf_counter()
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - started_at
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CalibreLibraryError(detail or f"{command[0]} failed after {elapsed:.2f}s")
    return result
