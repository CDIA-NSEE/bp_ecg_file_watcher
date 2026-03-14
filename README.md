# pdf-watcher

A Python application that monitors a directory for `.zip` files, decompresses them, validates that the internal PDF has exactly 2 pages, and routes the result accordingly.

## Stack

- **Runtime:** Python 3.12
- **Package manager:** `uv`
- **Linter/Formatter:** `ruff`
- **Type checker:** `pyrefly`
- **Task runner:** `taskipy`
- **Dependencies:** `watchdog`, `structlog`, `pypdf`, `zstandard`, `pydantic-settings`

## Setup

```bash
uv venv
uv sync
```

## Configuration

Copy `.env.example` to `.env` and set the required variables:

```bash
cp .env.example .env
```

Required variables:
- `WATCH_DIR` — directory to monitor for `.zip` files
- `OUTPUT_DIR` — directory for accepted (compressed) files
- `REJECTED_DIR` — directory for rejected files

## Usage

```bash
uv run pdf-watcher
# or
uv run task run
```

## Development

```bash
uv run task lint    # run ruff linter
uv run task format  # run ruff formatter
uv run task check   # run pyrefly type checker
uv run task dev     # lint + check + run
```

## Flow

```
Watchdog (dedicated thread)
    │  on_created → .zip files only
    ▼
FileQueue (queue.Queue with backpressure)
    │
    ▼
ThreadPoolExecutor (MAX_WORKERS threads) — each worker:
    1. wait_for_file_ready(path)
    2. check idempotency (in-flight set + Lock)
    3. decompress ZIP to tmp/<uuid4>/
    4. validate PDF pages (pypdf)
    │     ├── 2 pages  → compress with ZSTD level 9 → move to OUTPUT_DIR
    │     └── ≠ 2 pages → move original ZIP to REJECTED_DIR
    5. clean up tmp/<uuid4>/  (always, in finally block)
    6. task_done() on queue
```