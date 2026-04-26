from __future__ import annotations

import atexit
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading
from typing import Any

from flask import Flask, send_from_directory
from flask_sock import Sock
import usb1

from calibre_utils import CalibreHelperError, get_attached_device

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


app = Flask(__name__, static_folder=None)
sock.init_app(app)


def refresh_connected_e_reader() -> ConnectedEReader | None:
    global connected_e_reader

    try:
        device = get_attached_device()
    except CalibreHelperError as exc:
        app.logger.warning("Failed to refresh connected e-reader: %s", exc)
        device = None

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

    with usb1.USBContext() as context:
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


atexit.register(stop_libusb_event_loop)


def start_background_services() -> None:
    refresh_connected_e_reader()
    start_libusb_event_loop()


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
        ws.send(connected_e_reader_message())
        while ws.receive() is not None:
            pass
    finally:
        with stream_clients_lock:
            stream_clients.discard(ws)


if __name__ == "__main__":
    start_background_services()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
