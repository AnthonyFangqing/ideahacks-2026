# Server

Flask backend managed with `uv`.

```sh
uv sync
uv run flask --app app run --debug --port 3000
```

From the repo root, this also works through the pnpm workspace shim:

```sh
pnpm --filter server dev
```

Open http://localhost:3000.
