from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any


def scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [scalar(item) for item in value]
    if isinstance(value, list):
        return [scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): scalar(item) for key, item in value.items()}
    return value


def metadata_to_book(
    book_id: int,
    metadata: Any,
    formats: Iterable[str] = (),
    *,
    include_cover_url: bool = True,
) -> dict[str, Any]:
    identifiers = {}
    if hasattr(metadata, "get_identifiers"):
        identifiers = metadata.get_identifiers() or {}

    book = {
        "id": book_id,
        "title": getattr(metadata, "title", None) or "Unknown",
        "authors": list(getattr(metadata, "authors", None) or []),
        "tags": list(getattr(metadata, "tags", None) or []),
        "series": getattr(metadata, "series", None),
        "seriesIndex": getattr(metadata, "series_index", None),
        "publisher": getattr(metadata, "publisher", None),
        "languages": list(getattr(metadata, "languages", None) or []),
        "identifiers": identifiers,
        "uuid": getattr(metadata, "uuid", None),
        "formats": sorted(str(fmt).upper() for fmt in formats),
        "lastModified": scalar(getattr(metadata, "last_modified", None)),
        "publishedAt": scalar(getattr(metadata, "pubdate", None)),
    }
    if include_cover_url:
        book["coverUrl"] = f"/api/library/books/{book_id}/cover"
    return book


def device_book_to_dict(book: Any, storage: str) -> dict[str, Any]:
    path = getattr(book, "path", None) or getattr(book, "lpath", None)
    lpath = getattr(book, "lpath", None) or path
    collections = list(getattr(book, "device_collections", None) or [])
    return {
        "id": f"{storage}:{lpath}",
        "storage": storage,
        "path": path,
        "lpath": lpath,
        "title": getattr(book, "title", None) or Path(str(lpath)).stem,
        "authors": list(getattr(book, "authors", None) or []),
        "tags": list(getattr(book, "tags", None) or []),
        "collections": collections,
        "series": getattr(book, "series", None),
        "seriesIndex": getattr(book, "series_index", None),
        "size": getattr(book, "size", None),
        "mime": getattr(book, "mime", None),
        "applicationId": getattr(book, "application_id", None),
        "libraryBookId": getattr(book, "db_id", None),
    }


def device_info_to_dict(device: Any, info: tuple[Any, ...] | None) -> dict[str, Any]:
    if info is None:
        info = ()
    return {
        "driver": device.__class__.__name__,
        "name": info[0]
        if len(info) > 0
        else getattr(device, "gui_name", getattr(device, "name", "Unknown device")),
        "version": info[1] if len(info) > 1 else None,
        "softwareVersion": info[2] if len(info) > 2 else None,
        "mimeType": info[3] if len(info) > 3 else None,
        "supportsCollections": bool(getattr(device, "CAN_SET_METADATA", None)),
        "formats": [str(fmt).upper() for fmt in getattr(device, "FORMATS", [])],
    }
