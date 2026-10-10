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

from vhfwatch.models import Detection, Incident, Transcript, Transmission, new_id

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

    def __init__(self, path: Path | str, allow_threads: bool = False) -> None:
        # allow_threads=True is for the web app, whose sync endpoints run in
        # a threadpool: Python's sqlite3 is compiled serialized, so sharing
        # one connection across threads is safe (each statement locks).
        # The pipeline keeps the default — its writes stay on one loop.
        if allow_threads and sqlite3.threadsafety < 3:
            raise RuntimeError(
                "this Python's sqlite3 is not serialized "
                f"(threadsafety={sqlite3.threadsafety}); cannot share a "
                "connection across threads"
            )
        self._conn = sqlite3.connect(path, check_same_thread=not allow_threads)
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

    def list_transcripts(self, transmission_id: str) -> list[sqlite3.Row]:
        """All ASR passes over one transmission, oldest first.

        Usually one row, but re-transcription with a better model appends
        more — the web layer shows the newest and keeps the history visible.
        """
        cur = self._conn.execute(
            "SELECT * FROM transcript WHERE transmission_id = ? ORDER BY created_at",
            (transmission_id,),
        )
        return list(cur.fetchall())

    def recent_transcripts(self, limit: int) -> list[sqlite3.Row]:
        """Latest transcripts joined with their transmission times, newest
        first — the Tier 2 classifier's source of preceding-transmission
        context (docs/architecture.md §5.5)."""
        cur = self._conn.execute(
            """
            SELECT t.*, x.started_at AS tx_started_at
            FROM transcript t JOIN transmission x ON x.id = t.transmission_id
            ORDER BY x.started_at DESC LIMIT ?
            """,
            (limit,),
        )
        return list(cur.fetchall())

    # -- detections --------------------------------------------------------

    def insert_detection(self, d: Detection) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO detection
                    (id, transmission_id, severity, confidence, matched_terms,
                     tier, classifier_output, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d.id,
                    d.transmission_id,
                    d.severity.value,
                    d.confidence,
                    json.dumps(d.matched_terms),
                    d.tier,
                    json.dumps(d.classifier_output)
                    if d.classifier_output is not None
                    else None,
                    _iso(d.created_at),
                ),
            )

    def get_detection(self, detection_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM detection WHERE id = ?", (detection_id,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    # -- incidents ---------------------------------------------------------
    # The one mutable record (docs/decisions.md D9/D12): incidents stay open
    # and accrue, so unlike everything above they get genuine UPDATEs.

    def insert_incident(self, inc: Incident) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO incident
                    (id, opened_at, last_activity_at, closed_at, status,
                     severity, channels, summary, confidence, alerted_at,
                     acknowledged_at, acknowledged_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inc.id,
                    _iso(inc.opened_at),
                    _iso(inc.last_activity_at),
                    _iso(inc.closed_at) if inc.closed_at else None,
                    inc.status,
                    inc.severity.value,
                    json.dumps(inc.channels),
                    json.dumps(asdict(inc.summary)) if inc.summary else None,
                    inc.confidence,
                    _iso(inc.alerted_at) if inc.alerted_at else None,
                    _iso(inc.acknowledged_at) if inc.acknowledged_at else None,
                    inc.acknowledged_by,
                ),
            )
            for det_id in inc.detection_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO incident_member VALUES (?, ?, ?)",
                    (inc.id, det_id, _iso(inc.last_activity_at)),
                )

    def update_incident(self, inc: Incident) -> None:
        """Persist an incident's mutable state and any new members."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE incident SET
                    last_activity_at = ?, closed_at = ?, status = ?,
                    severity = ?, channels = ?, summary = ?, confidence = ?,
                    alerted_at = ?, acknowledged_at = ?, acknowledged_by = ?
                WHERE id = ?
                """,
                (
                    _iso(inc.last_activity_at),
                    _iso(inc.closed_at) if inc.closed_at else None,
                    inc.status,
                    inc.severity.value,
                    json.dumps(inc.channels),
                    json.dumps(asdict(inc.summary)) if inc.summary else None,
                    inc.confidence,
                    _iso(inc.alerted_at) if inc.alerted_at else None,
                    _iso(inc.acknowledged_at) if inc.acknowledged_at else None,
                    inc.acknowledged_by,
                    inc.id,
                ),
            )
            for det_id in inc.detection_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO incident_member VALUES (?, ?, ?)",
                    (inc.id, det_id, _iso(inc.last_activity_at)),
                )

    def ack_incident(self, incident_id: str, by: str) -> bool:
        """Record an acknowledgment; returns False for an unknown id.

        First ack wins — a second ack must not overwrite who actually
        responded, so rows already acknowledged are left untouched.
        """
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE incident SET acknowledged_at = ?, acknowledged_by = ?
                WHERE id = ? AND acknowledged_at IS NULL
                """,
                (_iso(datetime.now(UTC)), by, incident_id),
            )
        return cur.rowcount > 0

    def get_incident(self, incident_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM incident WHERE id = ?", (incident_id,))
        row: sqlite3.Row | None = cur.fetchone()
        return row

    def list_incidents(self, limit: int = 100) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM incident ORDER BY opened_at DESC LIMIT ?", (limit,)
        )
        return list(cur.fetchall())

    def list_open_incidents(self) -> list[sqlite3.Row]:
        """Open incidents, for the correlator to re-adopt after a restart."""
        cur = self._conn.execute(
            "SELECT * FROM incident WHERE status = 'open' ORDER BY opened_at"
        )
        return list(cur.fetchall())

    def incident_detections(self, incident_id: str) -> list[sqlite3.Row]:
        """The incident's detections in chronological order."""
        cur = self._conn.execute(
            """
            SELECT d.* FROM detection d
            JOIN incident_member m ON m.detection_id = d.id
            WHERE m.incident_id = ? ORDER BY d.created_at
            """,
            (incident_id,),
        )
        return list(cur.fetchall())

    # -- annotations (append-only; THE correction mechanism, D9) -----------

    def insert_annotation(
        self, subject_type: str, subject_id: str, author: str, body: str
    ) -> str:
        annotation_id = new_id()
        with self._conn:
            self._conn.execute(
                "INSERT INTO annotation VALUES (?, ?, ?, ?, ?, ?)",
                (
                    annotation_id,
                    subject_type,
                    subject_id,
                    author,
                    body,
                    _iso(datetime.now(UTC)),
                ),
            )
        return annotation_id

    def list_annotations(self, subject_type: str, subject_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            """
            SELECT * FROM annotation
            WHERE subject_type = ? AND subject_id = ? ORDER BY created_at
            """,
            (subject_type, subject_id),
        )
        return list(cur.fetchall())

    # -- alerts, tokens, access, health -------------------------------------

    def insert_alert(
        self,
        incident_id: str,
        channel: str,
        is_update: bool,
        ok: bool,
        detail: str | None,
    ) -> str:
        """Log one delivery attempt. Failures are logged too — a channel that
        silently never fires is the failure mode this project fears most."""
        alert_id = new_id()
        with self._conn:
            self._conn.execute(
                "INSERT INTO alert VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    alert_id,
                    incident_id,
                    channel,
                    int(is_update),
                    _iso(datetime.now(UTC)),
                    int(ok),
                    detail,
                ),
            )
        return alert_id

    def insert_share_token(
        self,
        incident_id: str,
        token_hash: str,
        recipient: str,
        expires_at: datetime,
    ) -> str:
        token_id = new_id()
        with self._conn:
            self._conn.execute(
                "INSERT INTO share_token VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    token_id,
                    incident_id,
                    token_hash,
                    recipient,
                    _iso(datetime.now(UTC)),
                    _iso(expires_at),
                ),
            )
        return token_id

    def get_share_token(self, token_hash: str) -> sqlite3.Row | None:
        """Look up a token by its hash — the raw token is never stored, so a
        leaked database cannot mint working links."""
        cur = self._conn.execute(
            "SELECT * FROM share_token WHERE token_hash = ?", (token_hash,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    def log_access(
        self, token_id: str | None, path: str, remote_addr: str | None
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO access_log (token_id, path, accessed_at, remote_addr)
                VALUES (?, ?, ?, ?)
                """,
                (token_id, path, _iso(datetime.now(UTC)), remote_addr),
            )

    def insert_health_event(
        self, stage: str, kind: str, detail: dict[str, object] | None = None
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO health_event (stage, kind, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    stage,
                    kind,
                    json.dumps(detail) if detail is not None else None,
                    _iso(datetime.now(UTC)),
                ),
            )

    def last_health_event_at(self, stage: str, kind: str) -> datetime | None:
        """When a given health event last happened, or None if never.

        Used to make "once per day" and "not during a crash loop" survive a
        restart: a process that forgets what it already sent would send a
        second daily check at every restart.
        """
        row = self._conn.execute(
            "SELECT MAX(created_at) AS t FROM health_event WHERE stage = ? AND kind = ?",
            (stage, kind),
        ).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    def count_transmissions_since(self, since: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM transmission WHERE started_at >= ?",
            (_iso(since),),
        ).fetchone()
        return int(row["n"])

    def last_transmission_at(self) -> str | None:
        """Most recent transmission start time — the primary liveness signal
        for /health (total silence on Ch 16 means something is broken)."""
        row = self._conn.execute(
            "SELECT MAX(started_at) AS t FROM transmission"
        ).fetchone()
        t: str | None = row["t"]
        return t

    def transmissions_after(self, after_id: str, limit: int = 50) -> list[sqlite3.Row]:
        """Transmissions newer than a ULID cursor, oldest first — feeds the
        /live SSE stream. ULIDs sort chronologically, so string comparison
        is the time comparison (docs/conventions.md §3)."""
        cur = self._conn.execute(
            "SELECT * FROM transmission WHERE id > ? ORDER BY id LIMIT ?",
            (after_id, limit),
        )
        return list(cur.fetchall())
