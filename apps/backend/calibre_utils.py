from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

CALIBRE_DEBUG_EXECUTABLE = "calibre-debug"
HELPER_JSON_PREFIX = "__BOOKSHELF_JSON__="
HELPER_SCRIPT = Path(__file__).with_name("calibre_utils_helper.py").read_bytes()


class CalibreHelperError(RuntimeError):
    """Raised if the Calibre helper script fails."""


def get_attached_device_books() -> list[dict[str, Any]]:
    """Return books in the attached e-reader's main memory."""

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
                env={},
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

    books = decoded.get("books")
    if not isinstance(books, list) or not all(isinstance(book, dict) for book in books):
        raise CalibreHelperError("Calibre helper returned an invalid book list")

    return books
