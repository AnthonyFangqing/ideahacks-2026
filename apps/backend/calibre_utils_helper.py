from __future__ import annotations

from calibre.customize.ui import device_plugins
from calibre.devices.scanner import DeviceScanner
from calibre.ebooks.metadata import authors_to_string
from calibre.utils.config import device_prefs
from calibre.utils.date import isoformat
from calibre.utils.localization import _ as _localize
import contextlib
import json
import sys
import traceback
from typing import Any

HELPER_JSON_PREFIX = "__BOOKSHELF_JSON__="


def main() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            device = get_connected_device()
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 1

    emit({"ok": True, "device": device})
    return 0


def get_connected_device() -> dict[str, Any] | None:
    try:
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

        opened_device = open_first_available_device(connected_devices, device_prefs)
        if opened_device is None:
            return None

        return {
            "name": get_device_name(opened_device),
            "books": [book_to_dict(book) for book in opened_device.books()],
        }
    finally:
        shutdown_plugins(device_plugins)


def open_first_available_device(connected_devices, device_prefs):
    for detected, plugin in connected_devices:
        try:
            plugin.open(detected, None)
        except Exception:
            continue

        plugin.specialize_global_preferences(device_prefs)
        return plugin

    return None


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
    }

    return result


def emit(payload: dict[str, Any]) -> None:
    print(HELPER_JSON_PREFIX + json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
