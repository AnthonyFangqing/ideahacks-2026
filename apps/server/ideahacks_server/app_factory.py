from __future__ import annotations

import atexit
import queue
from typing import Any

from flask import Flask, Response, jsonify, request, stream_with_context

from ideahacks_server.config import load_config
from ideahacks_server.device_service import DeviceService
from ideahacks_server.errors import ServiceError
from ideahacks_server.events import EventBus
from ideahacks_server.library_service import LibraryService
from ideahacks_server.transfer_service import TransferService


def create_app() -> Flask:
    config = load_config()
    events = EventBus()
    library = LibraryService(config, events)
    device = DeviceService(config, events)
    transfers = TransferService(library, device, events)

    app = Flask(__name__)
    app.config["IDEAHACKS_CONFIG"] = config
    app.extensions["events"] = events
    app.extensions["library"] = library
    app.extensions["device"] = device
    app.extensions["transfers"] = transfers

    device.start()
    atexit.register(transfers.shutdown)
    atexit.register(device.stop)
    atexit.register(library.close)

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = config.cors_origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET,POST,PATCH,DELETE,OPTIONS"
        )
        return response

    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError):
        return jsonify({"error": error.message}), error.status_code

    @app.errorhandler(404)
    def handle_not_found(_error: Exception):
        return jsonify({"error": "Not found"}), 404

    @app.route("/", methods=["GET"])
    def index() -> dict[str, Any]:
        return {
            "name": "Ideahacks backend",
            "status": "ok",
            "api": "/api",
            "events": "/api/events",
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "libraryPath": str(config.library_path),
            "calibre": {
                "libraryCommand": "calibredb",
                "deviceCommand": "ebook-device",
            },
            "device": device.status(),
        }

    @app.get("/api/events")
    def event_stream() -> Response:
        subscriber = events.subscribe()

        def generate():
            yield "event: ready\ndata: {}\n\n"
            try:
                while True:
                    try:
                        event = subscriber.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                    else:
                        yield event.to_sse()
            finally:
                events.unsubscribe(subscriber)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/library/books")
    def list_library_books() -> dict[str, Any]:
        return {"books": library.list_books(request.args.get("q"))}

    @app.post("/api/library/books")
    def upload_library_book():
        file = request.files.get("file")
        if file is None:
            raise ServiceError("Expected multipart upload field named 'file'")
        path = library.save_upload(file)
        try:
            book = library.add_book_from_path(path, delete_source=True)
        finally:
            path.unlink(missing_ok=True)
        return jsonify({"book": book}), 201

    @app.get("/api/library/books/<int:book_id>")
    def get_library_book(book_id: int) -> dict[str, Any]:
        return {"book": library.get_book(book_id)}

    @app.patch("/api/library/books/<int:book_id>")
    def update_library_book(book_id: int) -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        return {"book": library.update_book(book_id, payload)}

    @app.delete("/api/library/books/<int:book_id>")
    def delete_library_book(book_id: int):
        permanent = request.args.get("permanent") == "true"
        library.remove_book(book_id, permanent=permanent)
        return "", 204

    @app.get("/api/library/books/<int:book_id>/cover")
    def get_library_cover(book_id: int) -> Response:
        return Response(library.cover_bytes(book_id), mimetype="image/jpeg")

    @app.get("/api/library/collections")
    def list_library_collections() -> dict[str, Any]:
        return library.get_collections()

    @app.get("/api/device")
    def get_device_status() -> dict[str, Any]:
        return device.status()

    @app.get("/api/device/books")
    def list_device_books() -> dict[str, Any]:
        return {"books": device.list_books()}

    @app.get("/api/device/collections")
    def list_device_collections() -> dict[str, Any]:
        return {"collections": device.collections()}

    @app.post("/api/device/eject")
    def eject_device():
        device.eject()
        return "", 204

    @app.post("/api/transfers/library-to-device")
    def transfer_library_to_device() -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        book_ids = [int(book_id) for book_id in payload.get("bookIds", [])]
        storage = payload.get("target") or payload.get("storage") or "main"
        if not book_ids:
            raise ServiceError("Expected non-empty bookIds")
        return {"job": transfers.library_to_device(book_ids, storage=storage)}

    @app.post("/api/transfers/device-to-library")
    def transfer_device_to_library() -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        paths = [
            str(path) for path in payload.get("devicePaths", payload.get("paths", []))
        ]
        delete_after = bool(payload.get("deleteFromDeviceAfterCopy", False))
        if not paths:
            raise ServiceError("Expected non-empty devicePaths")
        return {
            "job": transfers.device_to_library(
                paths, delete_from_device_after_copy=delete_after
            )
        }

    @app.get("/api/transfers/<job_id>")
    def get_transfer_job(job_id: str) -> dict[str, Any]:
        return {"job": transfers.get_job(job_id)}

    return app
