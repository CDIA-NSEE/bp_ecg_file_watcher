# bp_ecg_file_watcher

A production-grade, containerised filesystem watcher that ingests **blood-pressure and ECG ZIP packages**, validates, rasterizes, compresses, and uploads them to a MinIO (S3-compatible) data lake — entirely **in-memory, with no intermediate disk writes**.

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start (Tutorial)](#quick-start-tutorial)
3. [Project Structure](#project-structure)
4. [Configuration Reference](#configuration-reference)
5. [How-To Guides](#how-to-guides)
6. [Architecture & Design](#architecture--design)
7. [Observability](#observability)
8. [CI / CD & Security](#ci--cd--security)
9. [Contributing](#contributing)

---

## Overview

`bp_ecg_file_watcher` monitors a directory for incoming **ZIP files**. Each ZIP is expected to contain a single **2-page PDF** — page 1 is a cover sheet, page 2 is the ECG/BP trace. When a stable ZIP is detected the service runs a fully in-memory pipeline:

```
ZIP detected → dedup check → PDF extracted → intake upload → validation
    → rasterize page 2 → resize image → re-encode as PDF
    → BLAKE3 hash → zstd compress → upload to copper bucket → record hash
```

Files that fail validation are routed to a **rejected bucket** (`coal`). Any unhandled exception routes the file to a **dead-letter queue** (`dlq`) after 3 exponential-backoff retries. Duplicate ZIPs are silently skipped.

### Key properties

| Property | Detail |
|---|---|
| **No temp files** | All processing lives in `BytesIO` — storage is never touched beyond the source ZIP. |
| **Backpressure** | Bounded `queue.Queue` blocks the watchdog thread when workers are saturated. |
| **Deduplication** | BLAKE3-hash of raw ZIP bytes stored in a thread-safe SQLite (WAL mode) database. |
| **Observability** | Prometheus counters, gauges, and histograms on port `8000`; structured JSON logs. |
| **Graceful shutdown** | `SIGTERM`/`SIGINT` drains the queue and waits for in-flight work before exiting. |

---

## Quick Start (Tutorial)

This tutorial gets the watcher running locally against a MinIO instance in under five minutes.

### Prerequisites

- Docker and Docker Compose
- `uv` package manager (`pip install uv`)
- A running MinIO instance (see step 1)

### Step 1 — Start MinIO locally

```bash
docker run -d \
  --name minio-dev \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  quay.io/minio/minio server /data --console-address ":9001"
```

Open [http://localhost:9001](http://localhost:9001), log in, and create four buckets:

| Bucket | Purpose |
|---|---|
| `bp-ecg-dev-copper` | Processed compressed PDFs (images) |
| `bp-ecg-dev-iron` | Original intake ZIPs (audit trail) |
| `bp-ecg-dev-coal` | Rejected files |
| `bp-ecg-dev-dlq` | Dead-letter queue |

### Step 2 — Create a `.env` file

```dotenv
WATCH_DIRECTORY=/tmp/bp_ecg_watch
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_USE_SSL=false
ENVIRONMENT=dev
LOG_LEVEL=DEBUG
```

### Step 3 — Run with `uv`

```bash
uv sync
mkdir -p /tmp/bp_ecg_watch
uv run task run
```

You should see structured logs on stdout. Drop a 2-page PDF wrapped in a ZIP into `/tmp/bp_ecg_watch/` and watch it process.

### Step 4 — Run with Docker

```bash
docker build -t bp_ecg_file_watcher .
docker run --rm \
  --env-file .env \
  -v /tmp/bp_ecg_watch:/watch \
  -v bp-ecg-watcher-dedup:/home/appuser/.bp_ecg \
  -p 8000:8000 \
  bp_ecg_file_watcher
```

> **Note:** The `-v bp-ecg-watcher-dedup:/home/appuser/.bp_ecg` volume persists the SQLite dedup database across container restarts. Without it every ZIP would be re-processed on restart.

---

## Project Structure

```
src/bp_ecg_watcher/
├── main.py              # Entry point — bootstraps everything, handles signals
├── config.py            # Pydantic-settings configuration (env vars / .env)
├── metrics.py           # Prometheus metric singletons
├── watcher/
│   ├── handler.py       # Watchdog FileSystemEventHandler — enqueues ZIP paths
│   └── debounce.py      # File-stability polling (for non-Linux platforms)
├── queue_manager/
│   └── dispatcher.py    # Consumer thread + ThreadPoolExecutor worker pool
├── processor/
│   ├── pipeline.py      # Orchestrates the full validate → upload pipeline
│   ├── extractor.py     # pypdfium2 PDF → PIL Image rasterizer
│   ├── image.py         # PIL Image resize + images → PDF bytes
│   ├── hasher.py        # BLAKE3 hashing
│   └── compressor.py    # Zstandard streaming compression + ByteCountingReader
├── validator/
│   └── pdf_validator.py # PDF page-count validation, typed result objects
├── storage/
│   └── minio_client.py  # boto3 upload helpers + metadata builders
└── dedup/
    └── store.py         # Peewee + SQLite WAL dedup store
```

---

## Configuration Reference

All settings are loaded from environment variables (or a `.env` file). No values are hardcoded.

| Variable | Type | Default | Description |
|---|---|---|---|
| `WATCH_DIRECTORY` | `Path` | **required** | Directory to watch for incoming ZIP files. |
| `MINIO_ENDPOINT` | `str` | **required** | MinIO/S3 endpoint URL (e.g. `http://minio:9000`). |
| `MINIO_ACCESS_KEY` | `str` | **required** | S3 access key. |
| `MINIO_SECRET_KEY` | `str` | **required** | S3 secret key. |
| `MINIO_USE_SSL` | `bool` | `false` | Enable TLS for MinIO connection. |
| `BUCKET_IMAGES` | `str` | `bp-ecg-dev-copper` | Destination bucket for processed PDFs. |
| `BUCKET_INTAKE` | `str` | `bp-ecg-dev-iron` | Bucket for raw intake ZIP archive copies. |
| `BUCKET_REJECTED` | `str` | `bp-ecg-dev-coal` | Bucket for rejected/invalid files. |
| `BUCKET_DLQ` | `str` | `bp-ecg-dev-dlq` | Dead-letter queue bucket (unrecoverable errors). |
| `MAX_WORKERS` | `int` | `4` | ThreadPoolExecutor worker thread count. |
| `QUEUE_MAXSIZE` | `int` | `50` | Max items in the bounded task queue. |
| `RASTERIZATION_DPI` | `int` | `300` | DPI used when rasterizing PDF pages to images. |
| `IMAGE_MAX_SIDE_PX` | `int` | `1200` | Maximum pixels on the longest side after resize. |
| `ZSTD_LEVEL` | `int` | `9` | Zstandard compression level (1–22). |
| `DEBOUNCE_POLLS` | `int` | `3` | Number of size-stability polls before enqueuing. |
| `DEBOUNCE_INTERVAL_MS` | `int` | `200` | Milliseconds between debounce polls. |
| `METRICS_PORT` | `int` | `8000` | TCP port for the Prometheus HTTP metrics endpoint. |
| `LOG_LEVEL` | `str` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `ENVIRONMENT` | `str` | `dev` | Runtime environment. `dev` enables console log rendering; any other value emits JSON. |

---

## How-To Guides

### How to run the full test suite

```bash
uv sync
uv run task test
```

Coverage must stay above 80%. To see a detailed HTML report:

```bash
uv run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

### How to run the complete CI gate locally

```bash
uv run task ci
```

This runs linting, format check, type checking, Bandit SAST, and the test suite in sequence — identical to the GitHub Actions pipeline.

### How to add a new bucket / upload destination

1. Add a new `bucket_*: str` field to `Settings` in [src/bp_ecg_watcher/config.py](src/bp_ecg_watcher/config.py).
2. Add the corresponding upload helper to [src/bp_ecg_watcher/storage/minio_client.py](src/bp_ecg_watcher/storage/minio_client.py) following the pattern of the existing helpers.
3. Call the helper from [src/bp_ecg_watcher/processor/pipeline.py](src/bp_ecg_watcher/processor/pipeline.py) at the appropriate pipeline step.
4. Add a matching environment variable to your `.env` and Dockerfile if it needs a different default in production.

### How to tune processing performance

- **Throughput**: Increase `MAX_WORKERS` and `QUEUE_MAXSIZE`. Workers are I/O-bound (MinIO upload), so 8–16 threads per core is reasonable.
- **Image quality vs. size**: Lower `RASTERIZATION_DPI` (e.g. `150`) and `IMAGE_MAX_SIDE_PX` (e.g. `800`) for faster processing and smaller output files.
- **Compression**: Lower `ZSTD_LEVEL` (e.g. `3`) for faster compression at slightly larger file sizes.

### How to extend validation rules

Edit `validate_pdf` in [src/bp_ecg_watcher/validator/pdf_validator.py](src/bp_ecg_watcher/validator/pdf_validator.py). Return a `ValidationFailure` with a new `RejectionReason` for any new rule. The pipeline will automatically route failing files to the rejected bucket without further changes.

---

## Architecture & Design

See [docs/explanation/architecture.md](docs/explanation/architecture.md) for a full design discussion, including the rationale behind key decisions (bounded queue, in-memory pipeline, WAL dedup store, typed validation results).

A visual system architecture diagram is available at [docs/diagrams/architecture.excalidraw](docs/diagrams/architecture.excalidraw).

---

## Observability

### Prometheus metrics

The service exposes metrics on `http://0.0.0.0:{METRICS_PORT}/metrics`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `watcher_files_processed_total` | Counter | `bucket` | Total ZIP files successfully processed. |
| `watcher_files_rejected_total` | Counter | `reason` | Total ZIP files rejected. `reason` is one of `invalid-page-count`, `corrupt-pdf`, `zip-read-error`. |
| `watcher_processing_duration_seconds` | Histogram | — | End-to-end processing time per ZIP file. Buckets: 0.5s, 1s, 2.5s, 5s, 10s, 30s, 60s. |
| `watcher_queue_depth` | Gauge | — | Current number of items waiting in the task queue. |

### Structured logs

In `ENVIRONMENT=dev` mode, logs are rendered to a colourful console format. In all other environments, logs are emitted as **newline-delimited JSON** (compatible with Loki, Datadog, Splunk, etc.).

Key log fields emitted at the `INFO`/`DEBUG` level include `source_zip`, `zip_hash`, `environment`, `watcher_version`, and per-step progress events throughout the pipeline.

---

## CI / CD & Security

### Workflows

| Workflow | Trigger | Description |
|---|---|---|
| `ci.yml` | Push / PR | Lint, format check, type check, tests, Docker build |
| `security.yml` | Push to `main`, PR, weekly | TruffleHog secrets, Bandit SAST, pip-audit CVE scan, Trivy container scan, OpenSSF Scorecard |
| `release.yml` | Tag `v*` | Builds and pushes Docker image to `ghcr.io` |

### Release

Tag a commit with a semver tag to publish a new container image to GitHub Container Registry:

```bash
git tag v1.2.3
git push origin v1.2.3
```

The image will be available at `ghcr.io/cdia-nsee/bp_ecg_file_watcher:v1.2.3`.

---

## Contributing

1. Fork the repo and create a feature branch.
2. Run `uv run task ci` — all gates must pass before opening a PR.
3. Follow the existing module docstring style (Args / Returns / Raises).
4. Keep coverage above 80%.
5. Update this README and the relevant docs page in `docs/` if you change observable behaviour.
