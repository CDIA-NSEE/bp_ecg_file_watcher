"""Queue manager package for the bp_ecg_file_watcher service.

Provides the dispatcher that bridges the watchdog event handler and the
ThreadPoolExecutor worker pool via a bounded queue.Queue.
"""
