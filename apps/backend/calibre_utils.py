from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
        candidates.extend([
            "/usr/bin/calibre-debug",
            "/opt/calibre/calibre-debug",
            "/usr/local/bin/calibre-debug",
        ])

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

    decoded = _run_helper()
    device = decoded.get("device")
    if device is None:
        return None
    if not isinstance(device, dict):
        raise CalibreHelperError("Calibre helper returned an invalid device")

    name = device.get("name")
    books = device.get("books")
    if not isinstance(name, str):
        raise CalibreHelperError("Calibre helper returned an invalid device name")
    if not isinstance(books, list) or not all(isinstance(book, dict) for book in books):
        raise CalibreHelperError("Calibre helper returned an invalid book list")

    return {"name": name, "books": books}


def get_attached_device_books() -> list[dict[str, Any]]:
    """Return books in the attached e-reader's main memory."""

    device = get_attached_device()
    if device is None:
        return []
    return device["books"]


def _run_helper() -> dict[str, Any]:
    try:
        with tempfile.NamedTemporaryFile(
            prefix="bookshelf_calibre_utils_helper"
        ) as helper:
            helper.write(HELPER_SCRIPT)
            helper.flush()
            result = subprocess.run(
                [CALIBRE_DEBUG_EXECUTABLE, "-e", helper.name],
                check=False,
                capture_output=True,
                text=True,
            )
    except FileNotFoundError as exc:
        raise CalibreHelperError(
            f"{CALIBRE_DEBUG_EXECUTABLE!r} was not found on PATH"
        ) from exc

    payload = None
    for line in result.stdout.splitlines():
        if line.startswith(HELPER_JSON_PREFIX):
            payload = line[len(HELPER_JSON_PREFIX) :]
            break

    if payload is None:
        detail = (result.stderr or result.stdout).strip()
        if result.returncode == 0:
            detail = detail or "Calibre helper produced no JSON payload"
        raise CalibreHelperError(detail)

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CalibreHelperError("Calibre helper returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise CalibreHelperError("Calibre helper returned an unexpected payload")

    if decoded.get("ok") is not True:
        error = decoded.get("error") or "Calibre helper failed"
        raise CalibreHelperError(str(error))

    return decoded
