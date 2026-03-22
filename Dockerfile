FROM python:3.12-slim AS builder
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /bin/sh -m appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/.venv .venv
COPY --chown=appuser:appgroup src/ src/
USER 1001
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
# Mount a named volume here to persist the SQLite dedup database across restarts.
# Example: docker run -v bp-ecg-watcher-dedup:/home/appuser/.bp_ecg ...
VOLUME ["/home/appuser/.bp_ecg"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bp_ecg_watcher.main"]
