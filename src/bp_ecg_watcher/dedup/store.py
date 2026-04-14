"""SQLite-backed deduplication store using peewee ORM.

Each :class:`DedupStore` instance manages its own SQLite database file.
WAL journal mode is enabled for safe concurrent reads from multiple worker
threads.  peewee's ``connection_context()`` opens and closes a thread-local
connection per operation, matching the former ``threading.local`` pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from peewee import CharField, Model, SqliteDatabase

_DEFAULT_DB_PATH: Path = Path.home() / ".bp_ecg" / "processed.db"


class DedupStore:
    """Peewee-backed deduplication store for processed ZIP hashes.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``~/.bp_ecg/processed.db``.  Pass a ``tmp_path``-based path in
            tests for isolation.
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: SqliteDatabase = SqliteDatabase(
            str(db_path),
            pragmas={"journal_mode": "wal"},
        )

        outer_db = self._db

        class _ProcessedFile(Model):
            zip_hash: CharField = CharField(primary_key=True)
            processed_at: CharField = CharField()
            source_path: CharField = CharField()
            destination_key: CharField = CharField()

            class Meta:
                database = outer_db
                table_name = "processed_files"

        self._model = _ProcessedFile

        with self._db.connection_context():
            self._db.create_tables([_ProcessedFile], safe=True)

    def is_duplicate(self, zip_hash: str) -> bool:
        """Return ``True`` when *zip_hash* already exists in the store.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.

        Returns:
            ``True`` if the hash is present; ``False`` otherwise.
        """
        with self._db.connection_context():
            return self._model.select().where(self._model.zip_hash == zip_hash).exists()

    def record_processed(
        self,
        zip_hash: str,
        source_path: Path,
        output_path: str,
    ) -> None:
        """Insert a processed ZIP entry, silently ignoring duplicates.

        Args:
            zip_hash: BLAKE3 hex digest of the raw ZIP bytes.
            source_path: Filesystem path of the processed ZIP.
            output_path: Path of the written output file.
        """
        with self._db.connection_context():
            self._model.get_or_create(
                zip_hash=zip_hash,
                defaults={
                    "processed_at": datetime.now(UTC).isoformat(),
                    "source_path": str(source_path),
                    "destination_key": output_path,
                },
            )
