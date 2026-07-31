"""SQLite access layer: WAL mode, versioned migrations, append-only writes.

Plain sqlite3 from the standard library — a single writer process on a
mostly-silent channel needs nothing more (docs/decisions.md D8), and an ORM
would just hide the append-only rules the schema enforces with triggers.

Write methods take the dataclasses from vhfwatch.models. PCM never goes in
the database; audio lives on disk and rows carry the path plus a SHA-256.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Self

import structlog

from vhfwatch.models import Transcript, Transmission, new_id

logger = structlog.get_logger(__name__)

# Migrations run in order; user_version records the last one applied.
# Append new (version, sql) entries — never edit a shipped one.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, resources.files("vhfwatch.store").joinpath("schema.sql").read_text()),
]


def _iso(dt: datetime) -> str:
    """Render a timestamp for storage, refusing naive datetimes.

    A naive datetime here means some stage produced local time; storing it
    would silently corrupt the timeline (docs/conventions.md §3).
    """
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed in the store: {dt!r}")
    return dt.astimezone(UTC).isoformat()


class Database:
    """One connection to the vhf-watch store. Not thread-safe; the pipeline
    is a single asyncio process and the web app opens its own read-only
    instance."""

    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        # WAL lets the web app read while the pipeline writes.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def migrate(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for version, sql in _MIGRATIONS:
            if version <= current:
                continue
            with self._conn:
                self._conn.executescript(sql)
                self._conn.execute(f"PRAGMA user_version = {version}")
            logger.info("db.migrated", version=version)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transmissions --------------------------------------------------

    def insert_transmission(self, tx: Transmission) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO transmission
                    (id, channel, started_at, ended_at, duration_ms,
                     sample_rate, rms_dbfs, peak_dbfs, est_snr_db,
                     audio_path, audio_sha256, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx.id,
                    tx.channel,
                    _iso(tx.started_at),
                    _iso(tx.ended_at),
                    tx.duration_ms,
                    tx.sample_rate,
                    tx.rms_dbfs,
                    tx.peak_dbfs,
                    tx.est_snr_db,
                    tx.audio_path,
                    tx.audio_sha256,
                    tx.source,
                ),
            )

    def get_transmission(self, transmission_id: str) -> sqlite3.Row | None:
        """Fetch one transmission row (metadata only — PCM is on disk)."""
        cur = self._conn.execute(
            "SELECT * FROM transmission WHERE id = ?", (transmission_id,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    def list_transmissions(self, limit: int = 100) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM transmission ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return list(cur.fetchall())

    # -- transcripts -----------------------------------------------------

    def insert_transcript(self, t: Transcript) -> str:
        """Append one ASR result. Returns the new row's id.

        Always an INSERT: re-transcribing a transmission adds a row, and the
        schema's trigger aborts any UPDATE (docs/decisions.md D9).
        """
        transcript_id = new_id()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO transcript
                    (id, transmission_id, engine, text, words,
                     avg_logprob, no_speech_prob, language, latency_ms,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript_id,
                    t.transmission_id,
                    t.engine,
                    t.text,
                    json.dumps([asdict(w) for w in t.words]),
                    t.avg_logprob,
                    t.no_speech_prob,
                    t.language,
                    t.latency_ms,
                    _iso(datetime.now(UTC)),
                ),
            )
        return transcript_id
