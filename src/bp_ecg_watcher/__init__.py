"""bp_ecg_watcher — file-watcher service for the bp_ecg datalakehouse pipeline.

Monitors a source directory for incoming ZIP files, validates the contained PDF,
rasterizes page 2, compresses the image with zstandard, and routes the result to
the appropriate MINIO bucket.
"""
