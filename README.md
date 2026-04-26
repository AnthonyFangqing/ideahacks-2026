# IdeaHacks 2026 Bookshelf

A digital ebook bookshelf and library kiosk for UCLA IdeaHacks 2026.

The project imagines a physical bookshelf-like kiosk that stores ebooks, detects a docked e-reader, displays a touch-friendly library interface, and helps people share books within a home or shared space. A Raspberry Pi runs the backend and talks to connected hardware, while a tablet or touchscreen displays the web frontend.

## Project Structure

```text
.
├── apps
│   ├── backend   # Flask server, Calibre integration, e-reader detection
│   └── frontend  # Vite + React kiosk UI
├── general_overview.md
├── package.json
└── pnpm-workspace.yaml
```

## Tech Stack

- Frontend: Vite, React, TypeScript
- Backend: Flask, Flask-Sock, Calibre, libusb
- Tooling: pnpm, Biome, Vitest, Husky

## Requirements

- Node.js and pnpm
- Python 3.14+
- Calibre with `calibre-debug` available on `PATH`
- Native libusb for e-reader hotplug detection

On macOS, install native libusb with Homebrew:

```sh
brew install libusb
```

Without libusb, the backend can still scan for an attached e-reader at startup through Calibre, but it will not auto-refresh on USB plug/unplug events.

## Getting Started

Install JavaScript dependencies:

```sh
pnpm install
```

Install backend dependencies from `apps/backend` using your Python environment manager of choice. The backend is configured with `pyproject.toml` and `uv.lock`.

```sh
cd apps/backend
uv sync
```

## Development

Start the frontend and backend together:

```sh
pnpm dev
```

Start the backend directly:

```sh
pnpm dev:backend
```

Start the frontend directly:

```sh
pnpm dev:frontend
```

The backend serves on `http://localhost:5005` and exposes a WebSocket stream at `/stream` with the current connected e-reader state.

The frontend defaults to `ws://localhost:5005/stream`. To point it somewhere else during development, set `VITE_BACKEND_URL`:

```sh
VITE_BACKEND_URL=http://localhost:5005 pnpm --filter web dev
```

## Verification

Run the frontend build:

```sh
pnpm build
```

Run linting:

```sh
pnpm lint
```

Run tests:

```sh
pnpm test
```

There are no test files yet, so the current test command exits successfully with `--passWithNoTests`.

## Current Status

- The backend can scan for a connected e-reader through Calibre and broadcast connection changes over WebSocket.
- The frontend is still close to the starter Vite/React app and needs the kiosk/library interface.
- The root build and lint commands pass.
- Automated tests still need to be added.
