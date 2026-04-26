# Family Bookshelf Kiosk

A tablet-first ebook kiosk for a family bookshelf. The system runs on a Raspberry Pi, keeps a local Calibre-backed library, detects connected e-readers, and lets people move books between the Pi and the e-reader with a warm, tactile web interface.

The project is intentionally local-first: the Pi stores the library, the tablet is the main control surface, and connected e-readers are managed through Calibre's supported command-line tools.

## What It Does

- Detects when an e-reader connects to the Pi and pushes live status updates to the frontend.
- Lists books and collections from both the Pi library and the connected e-reader.
- Transfers books from the Pi library to the e-reader.
- Imports books from the e-reader back into the Pi library.
- Supports drag-and-drop transfer flows in the web UI.
- Manages the Pi library: upload books, edit metadata, browse collections, and remove books.

## Project Structure

```txt
apps/
  server/  Flask API that talks to Calibre and connected e-readers
  web/     React + Vite single-page kiosk frontend
```

The root is a `pnpm` workspace. The backend is a Python project managed with `uv`.

## Requirements

- Node.js 20.19+ or 22.12+
- pnpm 10+
- Python 3.12+
- uv
- Calibre command-line tools available on `PATH`
  - `calibredb`
  - `ebook-device`

On the Raspberry Pi, install Calibre and make sure the user running the server can access the library directory and USB-connected e-readers.

## Setup

Install JavaScript dependencies from the repo root:

```sh
pnpm install
```

Install backend dependencies:

```sh
cd apps/server
uv sync
```

## Running Locally

Start the backend on port `3000`:

```sh
pnpm --filter server dev
```

Start the frontend on port `5173`:

```sh
pnpm --filter web dev
```

Then open:

```txt
http://localhost:5173
```

The Vite dev server proxies `/api` to `http://localhost:3000`, so the frontend can call the Flask backend without extra configuration during development.

You can also run both apps from the root:

```sh
pnpm dev
```

## Environment Variables

The backend supports these useful environment variables:

```sh
IDEAHACKS_LIBRARY_PATH=/var/lib/ideahacks/library
IDEAHACKS_CORS_ORIGIN=http://localhost:5173
```

For a Pi deployment, set `IDEAHACKS_LIBRARY_PATH` to the persistent folder where the Calibre library should live.

If the frontend is served from a different origin in production, set `IDEAHACKS_CORS_ORIGIN` to that origin.

## Backend API

Main endpoints:

- `GET /api/health` checks backend and device status.
- `GET /api/events` streams Server-Sent Events for device, library, and transfer changes.
- `GET /api/library/books` lists books in the Pi library.
- `POST /api/library/books` uploads a book into the Pi library.
- `PATCH /api/library/books/:id` updates book metadata.
- `DELETE /api/library/books/:id` removes a book from the Pi library.
- `GET /api/library/collections` lists tags, series, and authors from the Pi library.
- `GET /api/device` returns connected e-reader status.
- `GET /api/device/books` lists books on the connected e-reader.
- `GET /api/device/collections` lists collections on the connected e-reader.
- `POST /api/device/eject` ejects the connected e-reader.
- `POST /api/transfers/library-to-device` sends library books to the e-reader.
- `POST /api/transfers/device-to-library` imports e-reader books into the Pi library.
- `GET /api/transfers/:jobId` reads transfer job status.

The frontend subscribes to `/api/events` so it can react promptly when a device connects, disconnects, a library changes, or a transfer progresses.

## Frontend

The kiosk UI is a React SPA in `apps/web`.

Core views:

- Home: family-friendly overview and device status.
- Library: browse books and collections in the Pi library.
- Transfer: drag books onto the on-screen e-reader to send them, or drag e-reader books back to the library.
- Manage: upload books, edit metadata, remove books, and inspect collections.

The design direction matches the physical kiosk concept: plywood, paper, warm light, and a deliberate on-screen e-reader during transfer interactions.

## Checks

Build everything:

```sh
pnpm build
```

Lint and format checks:

```sh
pnpm lint
pnpm format
```

Run tests:

```sh
pnpm test
```

For only the web app:

```sh
pnpm --filter web build
pnpm --filter web lint
```

## Hardware Notes

The intended deployment is a Raspberry Pi mounted inside or near a plywood kiosk, with a tablet as the primary screen. The e-reader connects to the Pi over USB. The backend polls Calibre's `ebook-device info` command and publishes device events to the frontend when connection state changes.

If device detection works from a terminal but not from the service, check:

- USB permissions for the service user.
- Whether `ebook-device` is on the service user's `PATH`.
- Whether another Calibre process is already holding the e-reader connection.
- The configured Calibre library path and write permissions.
