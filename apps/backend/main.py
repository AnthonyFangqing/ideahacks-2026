from __future__ import annotations

import atexit
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock
import usb1
from werkzeug.utils import secure_filename

from calibre_library import (
    CalibreLibraryError,
    export_library_book,
    import_file_to_library,
    library_status,
    list_library_books,
)
from calibre_utils import (
    CalibreHelperError,
    delete_book_from_device,
    get_attached_device,
    import_book_from_device,
    send_book_to_device,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

sock = Sock()


@dataclass(frozen=True)
class ConnectedEReader:
    name: str
    books: list[dict[str, Any]]


connected_e_reader: ConnectedEReader | None = None
connected_e_reader_lock = threading.Lock()
stream_clients = set()
stream_clients_lock = threading.Lock()
libusb_stop_event = threading.Event()
libusb_context: usb1.USBContext | None = None
libusb_thread: threading.Thread | None = None
device_operation_lock = threading.Lock()


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
        ConnectedEReader(name=device["name"], books=device["books"])
        if device is not None
        else None
    )
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


def connected_e_reader_message() -> str:
    return json.dumps({"connected_e_reader": serialize_connected_e_reader()})


def broadcast_connected_e_reader() -> None:
    message = connected_e_reader_message()
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


@app.get("/api/library")
def api_library():
    query = request.args.get("query") or None
    try:
        books = list_library_books(query)
    except CalibreLibraryError as exc:
        return api_error(str(exc), 500)
    return jsonify({"library": library_status(), "books": books})


@app.get("/api/library/status")
def api_library_status():
    return jsonify({"library": library_status()})


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
    temp_paths: list[Path] = []
    try:
        for uploaded_file in uploaded_files:
            filename = secure_filename(uploaded_file.filename or "book")
            with tempfile.TemporaryDirectory(prefix="ideahacks_library_upload_") as temp_dir:
                temp_path = Path(temp_dir) / filename
                temp_paths.append(temp_path)
                uploaded_file.save(temp_path)
                added_ids.extend(
                    import_file_to_library(
                        str(temp_path),
                        delete_after_import=True,
                    )
                )
        books = list_library_books()
    except CalibreLibraryError as exc:
        for temp_path in temp_paths:
            temp_path.unlink(missing_ok=True)
        return api_error(str(exc), 500)

    return jsonify({
        "ok": True,
        "added_ids": added_ids,
        "library": library_status(),
        "books": books,
    })


@app.post("/api/device/send")
def api_send_to_device():
    payload = request.get_json(silent=True) or {}
    try:
        book_id = int(payload["book_id"])
    except (KeyError, TypeError, ValueError):
        return api_error("book_id is required", 400)

    requested_format = payload.get("format")
    if requested_format is not None and not isinstance(requested_format, str):
        return api_error("format must be a string", 400)

    exported: dict[str, Any] | None = None
    try:
        with device_operation_lock:
            exported = export_library_book(book_id, requested_format)
            transfer = send_book_to_device(
                exported["path"],
                exported["filename"],
                exported["book"],
            )
        reader = refresh_connected_e_reader()
    except (CalibreLibraryError, CalibreHelperError) as exc:
        return api_error(str(exc), 500)
    finally:
        if exported is not None:
            Path(exported["path"]).unlink(missing_ok=True)

    return jsonify({
        "ok": True,
        "transfer": transfer,
        "connected_e_reader": asdict(reader) if reader is not None else None,
    })


@app.post("/api/device/import")
def api_import_from_device():
    app.logger.info("device.import request received")
    payload = request.get_json(silent=True) or {}
    device_path = payload.get("device_path")
    if not isinstance(device_path, str) or not device_path:
        return api_error("device_path is required", 400)
    delete_after_import = bool(payload.get("delete_after_import", False))

    suffix = Path(device_path).suffix or ".book"
    temp_file = tempfile.NamedTemporaryFile(
        prefix="ideahacks_device_import_",
        suffix=suffix,
        delete=False,
    )
    temp_file.close()

    started_at = time.perf_counter()
    try:
        app.logger.info("device.import waiting for device_operation_lock")
        with device_operation_lock:
            app.logger.info(
                "device.import acquired device_operation_lock after %.2fs",
                time.perf_counter() - started_at,
            )
            if Path(device_path).is_file():
                app.logger.info("device.import copying mounted file: %s", device_path)
                shutil.copyfile(device_path, temp_file.name)
                imported = {"path": temp_file.name, "metadata": payload.get("metadata") or {}}
            else:
                app.logger.info("device.import copying via Calibre helper: %s", device_path)
                imported = import_book_from_device(device_path, temp_file.name)
            app.logger.info(
                "device.import copied file in %.2fs",
                time.perf_counter() - started_at,
            )
            added_ids = import_file_to_library(
                temp_file.name,
                imported.get("metadata"),
                delete_after_import=True,
            )
            app.logger.info(
                "device.import added to library in %.2fs",
                time.perf_counter() - started_at,
            )
            deleted = None
            if delete_after_import:
                if Path(device_path).is_file():
                    app.logger.info("device.import deleting mounted file: %s", device_path)
                    Path(device_path).unlink()
                    deleted = {"path": device_path}
                else:
                    deleted = delete_book_from_device(device_path)
            books = list_library_books()
        reader = refresh_connected_e_reader() if delete_after_import else current_connected_e_reader()
    except (CalibreLibraryError, CalibreHelperError) as exc:
        Path(temp_file.name).unlink(missing_ok=True)
        return api_error(str(exc), 500)

    return jsonify({
        "ok": True,
        "added_ids": added_ids,
        "deleted": deleted,
        "connected_e_reader": asdict(reader) if reader is not None else None,
        "books": books,
        "elapsed_seconds": round(time.perf_counter() - started_at, 2),
    })


@app.post("/api/device/delete")
def api_delete_from_device():
    payload = request.get_json(silent=True) or {}
    device_path = payload.get("device_path")
    if not isinstance(device_path, str) or not device_path:
        return api_error("device_path is required", 400)

    try:
        with device_operation_lock:
            deleted = delete_book_from_device(device_path)
        reader = refresh_connected_e_reader()
    except CalibreHelperError as exc:
        return api_error(str(exc), 500)

    return jsonify({
        "ok": True,
        "deleted": deleted,
        "connected_e_reader": asdict(reader) if reader is not None else None,
    })


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
