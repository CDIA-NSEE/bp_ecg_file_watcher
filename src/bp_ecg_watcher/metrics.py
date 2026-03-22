"""Prometheus metric singletons for bp_ecg_file_watcher.

All objects are module-level singletons. Import them wherever
telemetry is needed — never re-create them in other modules.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

files_processed_total: Counter = Counter(
    "watcher_files_processed_total",
    "Total number of ZIP files successfully processed",
    ["bucket"],
)

files_rejected_total: Counter = Counter(
    "watcher_files_rejected_total",
    "Total number of ZIP files rejected during validation",
    ["reason"],
)

processing_duration_seconds: Histogram = Histogram(
    "watcher_processing_duration_seconds",
    "Time spent processing a single ZIP file end-to-end",
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

queue_depth: Gauge = Gauge(
    "watcher_queue_depth",
    "Current number of items waiting in the task queue",
)
