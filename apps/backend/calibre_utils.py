from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _find_calibre_debug() -> str:
    """Find calibre-debug executable across platforms."""
    if path := shutil.which("calibre-debug"):
        return path

    # Platform-specific fallbacks
    candidates = []
    if sys.platform == "darwin":
        candidates.append("/Applications/calibre.app/Contents/MacOS/calibre-debug")
    elif sys.platform.startswith("linux"):
        candidates.extend(
            [
                "/usr/bin/calibre-debug",
                "/opt/calibre/calibre-debug",
                "/usr/local/bin/calibre-debug",
            ]
        )

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    raise CalibreHelperError(
        "calibre-debug not found. Install Calibre or ensure it's on PATH."
    )


CALIBRE_DEBUG_EXECUTABLE = _find_calibre_debug()
HELPER_JSON_PREFIX = "__BOOKSHELF_JSON__="
HELPER_SCRIPT = Path(__file__).with_name("calibre_utils_helper.py").read_bytes()


class CalibreHelperError(RuntimeError):
    """Raised if the Calibre helper script fails."""


def get_attached_device() -> dict[str, Any] | None:
    """Return the attached e-reader metadata, or None if no reader is attached."""

    try:
        decoded = _run_helper("scan")
    except CalibreHelperError:
        fallback = get_mounted_device_from_calibre_cache()
        if fallback is not None:
            return fallback
        raise
    device = decoded.get("device")
    if device is None:
        return get_mounted_device_from_calibre_cache()
    if not isinstance(device, dict):
        raise CalibreHelperError("Calibre helper returned an invalid device")

    name = device.get("name")
    books = device.get("books")
    if not isinstance(name, str):
        raise CalibreHelperError("Calibre helper returned an invalid device name")
    if not isinstance(books, list) or not all(isinstance(book, dict) for book in books):
        raise CalibreHelperError("Calibre helper returned an invalid book list")

    return {"name": name, "books": books}


def get_mounted_device_from_calibre_cache() -> dict[str, Any] | None:
    """Return mounted device metadata from Calibre's on-device cache.

    This lets the kiosk stay aware of a mounted reader when Calibre cannot scan
    the live device database, for example after KoboReader.sqlite corruption.
    """

    for root in mounted_device_roots():
        metadata_path = root / "metadata.calibre"
        if not metadata_path.is_file():
            continue
        try:
            raw_books = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw_books, list):
            continue

        books = []
        for raw_book in raw_books:
            if not isinstance(raw_book, dict):
                continue
            book = dict(raw_book)
            lpath = book.get("lpath")
            if isinstance(lpath, str) and lpath:
                book["path"] = str(root / lpath)
            books.append(book)

        return {"name": mounted_device_name(root), "books": books}
    return None


def mounted_device_roots() -> list[Path]:
    roots = []
    volumes = Path("/Volumes")
    if volumes.is_dir():
        for candidate in volumes.iterdir():
            if (candidate / "driveinfo.calibre").is_file():
                roots.append(candidate)
    return roots


def mounted_device_name(root: Path) -> str:
    driveinfo_path = root / "driveinfo.calibre"
    try:
        driveinfo = json.loads(driveinfo_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        driveinfo = {}
    name = driveinfo.get("device_name") if isinstance(driveinfo, dict) else None
    if isinstance(name, str) and name:
        return name
    return root.name


def get_attached_device_books() -> list[dict[str, Any]]:
    """Return books in the attached e-reader's main memory."""

    device = get_attached_device()
    if device is None:
        return []
    return device["books"]


def send_book_to_device(
    file_path: str,
    filename: str,
    metadata: dict[str, Any],
    on_card: str | None = None,
) -> dict[str, Any]:
    """Send a local book file to the attached e-reader."""

    decoded = _run_helper(
        "send_to_device",
        {
            "file_path": file_path,
            "filename": filename,
            "metadata": metadata,
            "on_card": on_card,
        },
    )
    transfer = decoded.get("transfer")
    if not isinstance(transfer, dict):
        raise CalibreHelperError("Calibre helper returned an invalid transfer result")
    return transfer


def import_book_from_device(device_path: str, output_path: str) -> dict[str, Any]:
    """Copy a book file from the attached e-reader to a local path."""

    decoded = _run_helper(
        "import_from_device",
        {"device_path": device_path, "output_path": output_path},
    )
    imported = decoded.get("imported")
    if not isinstance(imported, dict):
        raise CalibreHelperError("Calibre helper returned an invalid import result")
    return imported


def delete_book_from_device(device_path: str) -> dict[str, Any]:
    """Delete a book file from the attached e-reader."""

    decoded = _run_helper("delete_from_device", {"device_path": device_path})
    deleted = decoded.get("deleted")
    if not isinstance(deleted, dict):
        raise CalibreHelperError("Calibre helper returned an invalid delete result")
    return deleted


def get_device_book_cover(device_path: str) -> dict[str, Any] | None:
    """Return thumbnail bytes for a device book, if the driver exposes one."""

    decoded = _run_helper("cover_from_device", {"device_path": device_path})
    cover = decoded.get("cover")
    if cover is None:
        return None
    if not isinstance(cover, dict):
        raise CalibreHelperError("Calibre helper returned an invalid cover result")
    data = cover.get("data")
    media_type = cover.get("media_type")
    if not isinstance(data, str) or not isinstance(media_type, str):
        raise CalibreHelperError("Calibre helper returned an invalid cover payload")
    return {"data": base64.b64decode(data), "media_type": media_type}


def _run_helper(
    operation: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    request = {"operation": operation, "payload": payload or {}}
    started_at = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(
            prefix="bookshelf_calibre_utils_helper"
        ) as helper:
            helper.write(HELPER_SCRIPT)
            helper.flush()
            env = {"BOOKSHELF_HELPER_REQUEST": json.dumps(request)}
            result = subprocess.run(
                [CALIBRE_DEBUG_EXECUTABLE, "-e", helper.name],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
    except FileNotFoundError as exc:
        raise CalibreHelperError(
            f"{CALIBRE_DEBUG_EXECUTABLE!r} was not found on PATH"
        ) from exc

    elapsed = time.perf_counter() - started_at

    payload = None
    for line in result.stdout.splitlines():
        if line.startswith(HELPER_JSON_PREFIX):
            payload = line[len(HELPER_JSON_PREFIX) :]
            break

    if payload is None:
        detail = (result.stderr or result.stdout).strip()
        if result.returncode == 0:
            detail = detail or "Calibre helper produced no JSON payload"
        raise CalibreHelperError(f"{operation} failed after {elapsed:.2f}s: {detail}")

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CalibreHelperError("Calibre helper returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise CalibreHelperError("Calibre helper returned an unexpected payload")

    if decoded.get("ok") is not True:
        error = decoded.get("error") or "Calibre helper failed"
        raise CalibreHelperError(f"{operation} failed after {elapsed:.2f}s: {error}")

    decoded["_elapsed_seconds"] = round(elapsed, 2)
    return decoded
