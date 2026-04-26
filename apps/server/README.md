# Server

Flask backend managed with `uv`.

The backend integrates with the installed Calibre runtime through Calibre's
supported command-line tools:

- `calibredb` for the Pi library.
- `ebook-device` for connected e-readers.

```sh
uv sync
uv run flask --app app run --debug --port 3000
```

From the repo root, this also works through the pnpm workspace shim:

```sh
pnpm --filter server dev
```

Open http://localhost:3000.

Useful environment variables:

```sh
IDEAHACKS_LIBRARY_PATH=/var/lib/ideahacks/library
IDEAHACKS_CORS_ORIGIN=http://localhost:5173
```

Main API groups:

- `GET /api/events` streams Server-Sent Events for device, library, and transfer changes.
- `GET /api/library/books` lists the Pi Calibre library.
- `GET /api/device/books` lists books on the connected e-reader.
- `POST /api/transfers/library-to-device` sends library books to the e-reader.
- `POST /api/transfers/device-to-library` imports e-reader books into the Pi library.
