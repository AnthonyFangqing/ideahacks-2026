from __future__ import annotations

import atexit
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
import uuid

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from flask_sock import Sock
import usb1
from werkzeug.utils import secure_filename

from calibre_library import (
    CalibreLibraryError,
    delete_library_books,
    export_library_book,
    import_file_to_library_with_report,
    library_book_cover_path,
    library_status,
    list_library_books,
)
from calibre_utils import (
    CalibreHelperError,
    get_attached_device,
    get_device_book_cover,
    import_book_from_device,
    send_book_to_device,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

sock = Sock()


@dataclass(frozen=True)
class ConnectedEReader:
    name: str
    books: list[dict[str, Any]]


@dataclass
class TransferJob:
    id: str
    kind: str
    status: str = "queued"
    stage: str = "Queued"
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None


connected_e_reader: ConnectedEReader | None = None
connected_e_reader_lock = threading.Lock()
stream_clients = set()
stream_clients_lock = threading.Lock()
libusb_stop_event = threading.Event()
libusb_context: usb1.USBContext | None = None
libusb_thread: threading.Thread | None = None
device_operation_lock = threading.Lock()
transfer_jobs: dict[str, TransferJob] = {}
transfer_jobs_lock = threading.Lock()
device_cover_sources: dict[str, dict[str, Any]] = {}
device_cover_cache: dict[str, tuple[bytes, str]] = {}
device_cover_lock = threading.Lock()


app = Flask(__name__, static_folder=None)
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)
sock.init_app(app)


@app.after_request
def add_dev_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in {"http://localhost:5173", "http://127.0.0.1:5173"}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Vary"] = "Origin"
    return response


@app.route("/api/<path:_path>", methods=["OPTIONS"])
def api_options(_path: str):
    response = jsonify({"ok": True})
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def refresh_connected_e_reader() -> ConnectedEReader | None:
    global connected_e_reader

    started_at = time.perf_counter()
    with device_operation_lock:
        try:
            device = get_attached_device()
        except CalibreHelperError as exc:
            app.logger.warning("Failed to refresh connected e-reader: %s", exc)
            device = None
    app.logger.info(
        "device.refresh completed in %.2fs (%s)",
        time.perf_counter() - started_at,
        "attached" if device is not None else "none",
    )

    next_reader = (
        ConnectedEReader(name=device["name"], books=decorate_device_books(device["books"]))
        if device is not None
        else None
    )
    if next_reader is None:
        with device_cover_lock:
            device_cover_sources.clear()
    with connected_e_reader_lock:
        previous_reader = connected_e_reader
        connected_e_reader = next_reader
    if next_reader != previous_reader:
        broadcast_connected_e_reader()
    return next_reader


def current_connected_e_reader() -> ConnectedEReader | None:
    with connected_e_reader_lock:
        return connected_e_reader


def serialize_connected_e_reader() -> dict[str, Any] | None:
    reader = current_connected_e_reader()
    if reader is None:
        return None
    return asdict(reader)


def decorate_library_books(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated = []
    for book in books:
        next_book = dict(book)
        book_id = next_book.get("id")
        if isinstance(book_id, int):
            next_book["cover_url"] = f"/api/library/books/{book_id}/cover"
        decorated.append(next_book)
    return decorated


def decorate_device_books(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated = []
    next_sources: dict[str, dict[str, Any]] = {}
    for index, book in enumerate(books):
        next_book = dict(book)
        token = device_cover_token(next_book, index)
        next_book["cover_token"] = token
        next_book["cover_url"] = f"/api/device/books/{token}/cover"
        next_sources[token] = next_book
        decorated.append(next_book)

    with device_cover_lock:
        device_cover_sources.clear()
        device_cover_sources.update(next_sources)
    return decorated


def device_cover_token(book: dict[str, Any], index: int) -> str:
    identifiers = book.get("identifiers")
    identifier_key = (
        json.dumps(identifiers, sort_keys=True)
        if isinstance(identifiers, dict) and identifiers
        else None
    )
    raw_key = (
        book.get("path")
        or book.get("lpath")
        or identifier_key
        or "|".join(
            str(book.get(key) or "")
            for key in ("title", "authors_display", "publisher", "pubdate")
        )
        or str(index)
    )
    return hashlib.sha256(str(raw_key).encode("utf-8")).hexdigest()[:24]


def connected_e_reader_message() -> str:
    return json.dumps({
        "type": "device_state",
        "connected_e_reader": serialize_connected_e_reader(),
    })


def broadcast_stream_message(payload: dict[str, Any]) -> None:
    message = json.dumps(payload)
    with stream_clients_lock:
        clients = list(stream_clients)

    disconnected_clients = []
    for client in clients:
        try:
            client.send(message)
        except Exception:
            disconnected_clients.append(client)

    if disconnected_clients:
        with stream_clients_lock:
            for client in disconnected_clients:
                stream_clients.discard(client)


def broadcast_connected_e_reader() -> None:
    broadcast_stream_message({
        "type": "device_state",
        "connected_e_reader": serialize_connected_e_reader(),
    })


def start_libusb_event_loop() -> None:
    global libusb_thread

    if libusb_thread is not None and libusb_thread.is_alive():
        return

    libusb_stop_event.clear()
    libusb_thread = threading.Thread(
        target=libusb_event_loop,
        name="libusb-event-loop",
        daemon=True,
    )
    libusb_thread.start()


def stop_libusb_event_loop() -> None:
    libusb_stop_event.set()
    if libusb_context is not None:
        libusb_context.interruptEventHandler()
    if libusb_thread is not None and libusb_thread.is_alive():
        libusb_thread.join(timeout=2)


def libusb_event_loop() -> None:
    global libusb_context

    context = usb1.USBContext()
    try:
        context.open()
    except OSError as exc:
        app.logger.warning(
            "libusb hotplug monitoring is disabled: %s. "
            "Install native libusb to auto-refresh when devices are plugged in.",
            exc,
        )
        return

    try:
        libusb_context = context
        if not context.hasCapability(usb1.CAP_HAS_HOTPLUG):
            app.logger.warning("libusb hotplug events are not supported")
            return

        def on_libusb_event(_context, _device, _event) -> bool:
            app.logger.info(f"libusb event from device {_device}")
            refresh_connected_e_reader()
            return False

        callback_handle = context.hotplugRegisterCallback(
            on_libusb_event,
            events=usb1.HOTPLUG_EVENT_DEVICE_ARRIVED | usb1.HOTPLUG_EVENT_DEVICE_LEFT,
            flags=usb1.HOTPLUG_NO_FLAGS,
            vendor_id=usb1.HOTPLUG_MATCH_ANY,
            product_id=usb1.HOTPLUG_MATCH_ANY,
            dev_class=usb1.HOTPLUG_MATCH_ANY,
        )

        try:
            while not libusb_stop_event.is_set():
                context.handleEventsTimeout(1)
        finally:
            context.hotplugDeregisterCallback(callback_handle)
            libusb_context = None
    finally:
        libusb_context = None
        context.close()


atexit.register(stop_libusb_event_loop)


def start_background_services() -> None:
    refresh_connected_e_reader()
    start_libusb_event_loop()


def serialize_transfer_job(job: TransferJob) -> dict[str, Any]:
    return asdict(job)


def update_transfer_job(job: TransferJob, **updates: Any) -> None:
    with transfer_jobs_lock:
        for key, value in updates.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        serialized = serialize_transfer_job(job)
    broadcast_stream_message({"type": "transfer_job", "job": serialized})


def start_transfer_job(kind: str, work) -> TransferJob:
    job = TransferJob(id=uuid.uuid4().hex, kind=kind)
    with transfer_jobs_lock:
        transfer_jobs[job.id] = job
        serialized = serialize_transfer_job(job)
    broadcast_stream_message({"type": "transfer_job", "job": serialized})

    def run() -> None:
        started_at = time.perf_counter()
        update_transfer_job(job, status="running", stage="Starting", progress=0.05)
        try:
            result = work(job)
        except (CalibreLibraryError, CalibreHelperError) as exc:
            update_transfer_job(
                job,
                status="failed",
                stage="Failed",
                progress=1.0,
                error=str(exc),
                finished_at=time.time(),
            )
            app.logger.warning("transfer job %s failed: %s", job.id, exc)
            return
        except Exception:
            app.logger.exception("transfer job %s crashed", job.id)
            update_transfer_job(
                job,
                status="failed",
                stage="Failed",
                progress=1.0,
                error="Transfer job crashed",
                finished_at=time.time(),
            )
            return

        update_transfer_job(
            job,
            status="completed",
            stage="Done",
            progress=1.0,
            result=result,
            message=f"{kind} completed in {time.perf_counter() - started_at:.2f}s",
            finished_at=time.time(),
        )

    threading.Thread(target=run, name=f"transfer-job-{job.id}", daemon=True).start()
    return job


@app.get("/api/jobs/<job_id>")
def api_transfer_job(job_id: str):
    with transfer_jobs_lock:
        job = transfer_jobs.get(job_id)
        if job is None:
            return api_error("job was not found", 404)
        return jsonify({"job": serialize_transfer_job(job)})


@app.get("/api/library")
def api_library():
    query = request.args.get("query") or None
    try:
        books = decorate_library_books(list_library_books(query))
    except CalibreLibraryError as exc:
        return api_error(str(exc), 500)
    return jsonify({"library": library_status(), "books": books})


@app.get("/api/library/status")
def api_library_status():
    return jsonify({"library": library_status()})


@app.get("/api/library/books/<int:book_id>/cover")
def api_library_book_cover(book_id: int):
    try:
        cover_path = library_book_cover_path(book_id)
    except CalibreLibraryError:
        cover_path = None

    if cover_path is None:
        return placeholder_cover_response()
    return send_file(
        cover_path,
        mimetype="image/jpeg",
        conditional=True,
        max_age=3600,
    )


@app.get("/api/device/books/<cover_token>/cover")
def api_device_book_cover(cover_token: str):
    with device_cover_lock:
        cached = device_cover_cache.get(cover_token)
        source = device_cover_sources.get(cover_token)
    if cached is not None:
        data, media_type = cached
        return cover_bytes_response(data, media_type)
    if source is None:
        return placeholder_cover_response()

    if matched_cover_path := find_matching_library_cover(source):
        return send_file(
            matched_cover_path,
            mimetype="image/jpeg",
            conditional=True,
            max_age=3600,
        )

    device_path = source.get("path") or source.get("lpath")
    if isinstance(device_path, str) and device_path:
        try:
            with device_operation_lock:
                cover = get_device_book_cover(device_path)
        except CalibreHelperError as exc:
            app.logger.info("device cover lookup failed for %s: %s", device_path, exc)
            cover = None
        if cover is not None:
            data = cover["data"]
            media_type = cover["media_type"]
            with device_cover_lock:
                device_cover_cache[cover_token] = (data, media_type)
            return cover_bytes_response(data, media_type)

    return placeholder_cover_response()


def cover_bytes_response(data: bytes, media_type: str) -> Response:
    response = Response(data, mimetype=media_type)
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["ETag"] = hashlib.sha256(data).hexdigest()
    return response


def placeholder_cover_response() -> Response:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="240" height="360" viewBox="0 0 240 360">
<rect width="240" height="360" rx="14" fill="#f7efe1"/>
<rect x="26" y="28" width="188" height="304" rx="8" fill="#fffaf1" stroke="#d9c9ad" stroke-width="2"/>
<path d="M68 118h104M68 146h104M68 174h74" stroke="#8f6f49" stroke-width="10" stroke-linecap="round"/>
<path d="M72 245h96M72 273h64" stroke="#d9c9ad" stroke-width="8" stroke-linecap="round"/>
</svg>"""
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def find_matching_library_cover(device_book: dict[str, Any]) -> Path | None:
    try:
        library_books = list_library_books()
    except CalibreLibraryError:
        return None

    for library_book in library_books:
        if books_match(device_book, library_book):
            book_id = library_book.get("id")
            if isinstance(book_id, int):
                try:
                    if cover_path := library_book_cover_path(book_id):
                        return cover_path
                except CalibreLibraryError:
                    continue
    return None


def books_match(device_book: dict[str, Any], library_book: dict[str, Any]) -> bool:
    device_identifiers = device_book.get("identifiers")
    library_identifiers = library_book.get("identifiers")
    if isinstance(device_identifiers, dict) and isinstance(library_identifiers, dict):
        for key, value in device_identifiers.items():
            if value and library_identifiers.get(key) == value:
                return True

    return (
        bool(normalize_match_text(device_book.get("title")))
        and normalize_match_text(device_book.get("title"))
        == normalize_match_text(library_book.get("title"))
        and normalize_author_list(device_book) == normalize_author_list(library_book)
    )


def normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def normalize_author_list(book: dict[str, Any]) -> str:
    authors = book.get("authors")
    if isinstance(authors, list):
        return " & ".join(normalize_match_text(author) for author in authors)
    return normalize_match_text(book.get("authors_display"))


def request_int_list(payload: dict[str, Any], plural_key: str, singular_key: str) -> list[int]:
    raw_items = payload.get(plural_key)
    if raw_items is None and singular_key in payload:
        raw_items = [payload[singular_key]]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError(f"{plural_key} is required")
    return [int(item) for item in raw_items]


def request_device_imports(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("books")
    if raw_items is None and "device_path" in payload:
        raw_items = [{
            "device_path": payload.get("device_path"),
            "metadata": payload.get("metadata"),
        }]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("books is required")

    imports: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("books must contain objects")
        device_path = item.get("device_path")
        if not isinstance(device_path, str) or not device_path:
            raise ValueError("each book needs a device_path")
        metadata = item.get("metadata")
        imports.append({
            "device_path": device_path,
            "metadata": metadata if isinstance(metadata, dict) else {},
        })
    return imports


@app.get("/api/device")
def api_device():
    refresh = request.args.get("refresh") != "false"
    reader = refresh_connected_e_reader() if refresh else current_connected_e_reader()
    return jsonify({"connected_e_reader": asdict(reader) if reader is not None else None})


@app.post("/api/library/import")
def api_import_to_library():
    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        return api_error("At least one book file is required", 400)

    added_ids: list[int] = []
    duplicates: list[dict[str, Any]] = []
    temp_paths: list[Path] = []
    try:
        for uploaded_file in uploaded_files:
            filename = secure_filename(uploaded_file.filename or "book")
            with tempfile.TemporaryDirectory(prefix="ideahacks_library_upload_") as temp_dir:
                temp_path = Path(temp_dir) / filename
                temp_paths.append(temp_path)
                uploaded_file.save(temp_path)
                import_result = import_file_to_library_with_report(
                    str(temp_path),
                    delete_after_import=True,
                )
                added_ids.extend(import_result["added_ids"])
                duplicates.extend(import_result["duplicates"])
        books = decorate_library_books(list_library_books())
    except CalibreLibraryError as exc:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        return api_error(str(exc), 500)

    return jsonify({
        "ok": True,
        "added_ids": added_ids,
        "duplicates": duplicates,
        "library": library_status(),
        "books": books,
    })


@app.post("/api/library/delete")
def api_delete_from_library():
    payload = request.get_json(silent=True) or {}
    try:
        book_ids = request_int_list(payload, "book_ids", "book_id")
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), 400)

    def work(job: TransferJob) -> dict[str, Any]:
        total = len(book_ids)
        update_transfer_job(
            job,
            stage=f"Deleting {total} book{'s' if total != 1 else ''} from library",
            progress=0.25,
        )
        delete_library_books(book_ids)
        update_transfer_job(job, stage="Refreshing library", progress=0.8)
        books = decorate_library_books(list_library_books())
        return {
            "ok": True,
            "deleted_ids": book_ids,
            "library": library_status(),
            "books": books,
        }

    job = start_transfer_job("delete_from_library", work)
    return jsonify({"ok": True, "job": serialize_transfer_job(job)}), 202


@app.post("/api/device/send")
def api_send_to_device():
    payload = request.get_json(silent=True) or {}
    try:
        book_ids = request_int_list(payload, "book_ids", "book_id")
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), 400)

    requested_format = payload.get("format")
    if requested_format is not None and not isinstance(requested_format, str):
        return api_error("format must be a string", 400)

    def work(job: TransferJob) -> dict[str, Any]:
        exported_books: list[dict[str, Any]] = []
        transfers: list[dict[str, Any]] = []
        try:
            update_transfer_job(job, stage="Waiting for reader", progress=0.1)
            with device_operation_lock:
                total = len(book_ids)
                for index, book_id in enumerate(book_ids, start=1):
                    update_transfer_job(
                        job,
                        stage=f"Exporting {index}/{total} from library",
                        progress=0.15 + ((index - 1) / total) * 0.3,
                    )
                    exported = export_library_book(book_id, requested_format)
                    exported_books.append(exported)
                    update_transfer_job(
                        job,
                        stage=f"Sending {index}/{total} to reader",
                        progress=0.45 + ((index - 1) / total) * 0.35,
                    )
                    transfers.append(send_book_to_device(
                        exported["path"],
                        exported["filename"],
                        exported["book"],
                    ))
            update_transfer_job(job, stage="Refreshing reader", progress=0.85)
            reader = refresh_connected_e_reader()
        finally:
            for exported in exported_books:
                Path(exported["path"]).unlink(missing_ok=True)

        return {
            "ok": True,
            "transfers": transfers,
            "connected_e_reader": asdict(reader) if reader is not None else None,
        }

    job = start_transfer_job("send_to_device", work)
    return jsonify({"ok": True, "job": serialize_transfer_job(job)}), 202


@app.post("/api/device/import")
def api_import_from_device():
    app.logger.info("device.import request received")
    payload = request.get_json(silent=True) or {}
    try:
        imports = request_device_imports(payload)
    except ValueError as exc:
        return api_error(str(exc), 400)
    if payload.get("delete_after_import"):
        return api_error("Moving books off the reader is no longer supported", 400)

    def work(job: TransferJob) -> dict[str, Any]:
        started_at = time.perf_counter()
        temp_paths: list[Path] = []
        added_ids: list[int] = []
        duplicates: list[dict[str, Any]] = []
        try:
            app.logger.info("device.import waiting for device_operation_lock")
            update_transfer_job(job, stage="Waiting for reader", progress=0.1)
            with device_operation_lock:
                app.logger.info(
                    "device.import acquired device_operation_lock after %.2fs",
                    time.perf_counter() - started_at,
                )
                total = len(imports)
                for index, item in enumerate(imports, start=1):
                    device_path = item["device_path"]
                    suffix = Path(device_path).suffix or ".book"
                    temp_file = tempfile.NamedTemporaryFile(
                        prefix="ideahacks_device_import_",
                        suffix=suffix,
                        delete=False,
                    )
                    temp_file.close()
                    temp_path = Path(temp_file.name)
                    temp_paths.append(temp_path)

                    update_transfer_job(
                        job,
                        stage=f"Copying {index}/{total} from reader",
                        progress=0.15 + ((index - 1) / total) * 0.3,
                    )
                    imported = import_book_from_device(device_path, str(temp_path))
                    update_transfer_job(
                        job,
                        stage=f"Adding {index}/{total} to library",
                        progress=0.45 + ((index - 1) / total) * 0.25,
                    )
                    import_result = import_file_to_library_with_report(
                        str(temp_path),
                        imported.get("metadata") or item["metadata"],
                        delete_after_import=True,
                    )
                    added_ids.extend(import_result["added_ids"])
                    duplicates.extend(import_result["duplicates"])
                update_transfer_job(job, stage="Refreshing library", progress=0.85)
                books = decorate_library_books(list_library_books())
            reader = current_connected_e_reader()
        except Exception:
            for temp_path in temp_paths:
                temp_path.unlink(missing_ok=True)
            raise

        return {
            "ok": True,
            "added_ids": added_ids,
            "duplicates": duplicates,
            "connected_e_reader": asdict(reader) if reader is not None else None,
            "books": books,
            "elapsed_seconds": round(time.perf_counter() - started_at, 2),
        }

    job = start_transfer_job("import_from_device", work)
    return jsonify({"ok": True, "job": serialize_transfer_job(job)}), 202


def api_error(message: str, status_code: int):
    return jsonify({"ok": False, "error": message}), status_code


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(FRONTEND_DIR, path)


@sock.route("/stream")
def stream(ws) -> None:
    with stream_clients_lock:
        stream_clients.add(ws)
    try:
        try:
            ws.send(connected_e_reader_message())
            with transfer_jobs_lock:
                jobs = list(transfer_jobs.values())
            for job in jobs:
                ws.send(json.dumps({
                    "type": "transfer_job",
                    "job": serialize_transfer_job(job),
                }))
        except Exception:
            return
        while True:
            try:
                if ws.receive() is None:
                    break
            except Exception:
                break
    finally:
        with stream_clients_lock:
            stream_clients.discard(ws)


if __name__ == "__main__":
    start_background_services()
    app.run(host="0.0.0.0", port=5005, debug=True, use_reloader=False)
