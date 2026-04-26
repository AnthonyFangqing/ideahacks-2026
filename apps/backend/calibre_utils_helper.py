from __future__ import annotations

from calibre.customize.ui import device_plugins
from calibre.devices.scanner import DeviceScanner
from calibre.ebooks.metadata import authors_to_string
from calibre.ebooks.metadata.book.base import Metadata
from calibre.utils.config import device_prefs
from calibre.utils.date import isoformat
from calibre.utils.localization import _ as _localize
import contextlib
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Any

HELPER_JSON_PREFIX = "__BOOKSHELF_JSON__="


def main() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            request = read_request()
            operation = request.get("operation", "scan")
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Helper payload must be an object")

            if operation == "scan":
                result = {"device": get_connected_device()}
            elif operation == "send_to_device":
                result = {"transfer": send_to_device(payload)}
            elif operation == "import_from_device":
                result = {"imported": import_from_device(payload)}
            elif operation == "delete_from_device":
                result = {"deleted": delete_from_device(payload)}
            else:
                raise ValueError(f"Unknown helper operation: {operation}")
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 1

    emit({"ok": True, **result})
    return 0


def read_request() -> dict[str, Any]:
    raw = os.environ.get("BOOKSHELF_HELPER_REQUEST")
    if not raw:
        return {"operation": "scan", "payload": {}}
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Helper request must be an object")
    return decoded


def get_connected_device() -> dict[str, Any] | None:
    opened_device = None
    try:
        opened_device = open_connected_device()
        if opened_device is None:
            return None

        return {
            "name": get_device_name(opened_device),
            "books": [book_to_dict(book) for book in opened_device.books()],
        }
    finally:
        close_device(opened_device)
        shutdown_plugins(device_plugins)


def open_connected_device():
    scanner = DeviceScanner()
    scanner.scan()

    connected_devices = []
    selected_device = None

    for plugin in device_plugins():
        try:
            plugin.startup()
        except Exception:
            print(f"Startup failed for device plugin: {plugin}", file=sys.stderr)

        if plugin.MANAGES_DEVICE_PRESENCE:
            detected = plugin.detect_managed_devices(scanner.devices)
            if detected is not None:
                connected_devices.append((detected, plugin))
                selected_device = plugin
                break
            continue

        ok, detected = scanner.is_device_connected(plugin)
        if ok:
            selected_device = plugin
            selected_device.reset(detected_device=detected)
            connected_devices.append((detected, selected_device))

    if selected_device is None:
        return None

    return open_first_available_device(connected_devices, device_prefs)


def open_first_available_device(connected_devices, device_prefs):
    for detected, plugin in connected_devices:
        try:
            plugin.open(detected, None)
        except Exception:
            continue

        plugin.specialize_global_preferences(device_prefs)
        return plugin

    return None


def send_to_device(payload: dict[str, Any]) -> dict[str, Any]:
    file_path = require_string(payload, "file_path")
    filename = require_string(payload, "filename")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    on_card = payload.get("on_card")
    if on_card is not None and on_card not in {"carda", "cardb"}:
        raise ValueError("on_card must be carda, cardb, or null")

    opened_device = None
    try:
        opened_device = open_connected_device()
        if opened_device is None:
            raise RuntimeError("No e-reader is attached")

        mi = metadata_to_calibre_metadata(metadata)
        locations = opened_device.upload_books(
            [file_path],
            [filename],
            on_card=on_card,
            end_session=False,
            metadata=[mi],
        )
        booklists = get_device_booklists(opened_device)
        opened_device.add_books_to_metadata(locations, [mi], booklists)
        opened_device.sync_booklists(booklists, end_session=False)

        return {
            "device_name": get_device_name(opened_device),
            "locations": [serialize_location(location) for location in locations],
        }
    finally:
        close_device(opened_device)
        shutdown_plugins(device_plugins)


def import_from_device(payload: dict[str, Any]) -> dict[str, Any]:
    device_path = require_string(payload, "device_path")
    output_path = require_string(payload, "output_path")

    opened_device = None
    try:
        opened_device = open_connected_device()
        if opened_device is None:
            raise RuntimeError("No e-reader is attached")

        book = find_device_book(opened_device, device_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as outfile:
            opened_device.get_file(device_path, outfile, end_session=False)

        return {
            "device_name": get_device_name(opened_device),
            "path": output_path,
            "metadata": book_to_dict(book) if book is not None else {},
        }
    finally:
        close_device(opened_device)
        shutdown_plugins(device_plugins)


def delete_from_device(payload: dict[str, Any]) -> dict[str, Any]:
    device_path = require_string(payload, "device_path")

    opened_device = None
    try:
        opened_device = open_connected_device()
        if opened_device is None:
            raise RuntimeError("No e-reader is attached")

        booklists = get_device_booklists(opened_device)
        opened_device.delete_books([device_path], end_session=False)
        try:
            opened_device.remove_books_from_metadata([device_path], booklists)
            opened_device.sync_booklists(booklists, end_session=False)
        except Exception:
            print("Failed to update device metadata after delete", file=sys.stderr)
            traceback.print_exc()

        return {"device_name": get_device_name(opened_device), "path": device_path}
    finally:
        close_device(opened_device)
        shutdown_plugins(device_plugins)


def get_device_booklists(device):
    booklists = []
    for oncard in (None, "carda", "cardb"):
        try:
            booklists.append(device.books(oncard=oncard, end_session=False))
        except Exception:
            booklists.append(None)
    return tuple(booklists)


def find_device_book(device, device_path: str):
    for oncard in (None, "carda", "cardb"):
        try:
            books = device.books(oncard=oncard, end_session=False)
        except Exception:
            continue
        for book in books:
            if device_path in {
                getattr(book, "path", None),
                getattr(book, "lpath", None),
                "/" + str(getattr(book, "lpath", "")).lstrip("/"),
            }:
                return book
    return None


def metadata_to_calibre_metadata(raw: dict[str, Any]) -> Metadata:
    title = str(raw.get("title") or "Untitled")
    authors = raw.get("authors") or raw.get("authors_display") or [_localize("Unknown")]
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.replace("&", ",").split(",") if part.strip()]
    mi = Metadata(title, authors)

    for attr in ("publisher", "series", "comments", "author_sort", "title_sort"):
        if value := raw.get(attr):
            setattr(mi, attr, str(value))
    if raw.get("series_index") is not None:
        try:
            mi.series_index = float(raw["series_index"])
        except (TypeError, ValueError):
            pass
    tags = raw.get("tags")
    if isinstance(tags, list):
        mi.tags = [str(tag) for tag in tags]
    languages = raw.get("languages")
    if isinstance(languages, list):
        mi.languages = [str(language) for language in languages]
    identifiers = raw.get("identifiers")
    if isinstance(identifiers, dict):
        mi.identifiers = {str(key): str(value) for key, value in identifiers.items()}

    return mi


def require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def serialize_location(location) -> list[str | None]:
    return [str(part) if part is not None else None for part in location]


def get_device_name(device) -> str:
    try:
        name = device.get_device_information()[0]
    except Exception:
        name = None

    if name:
        return str(name)
    return str(getattr(device, "name", None) or device.__class__.__name__)


def shutdown_plugins(device_plugins) -> None:
    for plugin in device_plugins():
        try:
            plugin.shutdown()
        except Exception:
            pass


def close_device(device) -> None:
    if device is None:
        return
    try:
        device.close()
    except Exception:
        pass


def book_to_dict(book) -> dict[str, Any]:
    result = {
        "title": str(book.title),
        "title_sort": str(book.title_sort) if book.title_sort else None,
        "authors": list(book.authors) if book.authors else [],
        "authors_display": authors_to_string(book.authors) if book.authors else "",
        "author_sort": (
            str(book.author_sort)
            if book.author_sort and book.author_sort != _localize("Unknown")
            else None
        ),
        "publisher": str(book.publisher) if book.publisher else None,
        "book_producer": (
            str(book.book_producer) if getattr(book, "book_producer", False) else None
        ),
        "tags": [str(tag) for tag in book.tags] if book.tags else [],
        "series": str(book.series) if book.series else None,
        "series_index": (str(book.format_series_index()) if book.series else None),
        "languages": (
            [str(language) for language in book.languages]
            if not book.is_null("languages")
            else []
        ),
        "rating": (float(book.rating) if book.rating is not None else None),
        "timestamp": isoformat(book.timestamp) if book.timestamp is not None else None,
        "pubdate": isoformat(book.pubdate) if book.pubdate is not None else None,
        "rights": str(book.rights) if book.rights is not None else None,
        "identifiers": (
            {k: str(v) for k, v in book.identifiers.items()}
            if book.identifiers
            else None
        ),
        "comments": str(book.comments) if book.comments else None,
        "path": str(book.path) if getattr(book, "path", None) else None,
        "lpath": str(book.lpath) if getattr(book, "lpath", None) else None,
        "mime": str(book.mime) if getattr(book, "mime", None) else None,
        "size": int(book.size) if getattr(book, "size", None) is not None else None,
    }

    return result


def emit(payload: dict[str, Any]) -> None:
    print(HELPER_JSON_PREFIX + json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
