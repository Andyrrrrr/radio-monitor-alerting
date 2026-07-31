"""Store layer: migrations, roundtrips, and the append-only guarantees.

The append-only tests matter most here — they pin the D9 triggers so a
future schema change can't quietly drop them (docs/decisions.md D9).
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from vhfwatch.models import Transcript, Transmission, new_id
from vhfwatch.store import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.migrate()
    return database


def make_transmission(**overrides: object) -> Transmission:
    started = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    tx = Transmission(
        id=new_id(),
        channel="16",
        started_at=started,
        ended_at=started + timedelta(seconds=4),
        duration_ms=4000,
        pcm=np.zeros(64000, dtype=np.float32),
        sample_rate=16000,
        rms_dbfs=-20.0,
        peak_dbfs=-6.0,
        est_snr_db=15.0,
        audio_path=None,
        audio_sha256=None,
        source="file:test.wav",
    )
    for key, value in overrides.items():
        setattr(tx, key, value)
    return tx


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    with Database(path) as database:
        database.migrate()
        database.migrate()  # second run must be a no-op, not a re-CREATE
    # A fresh connection to the same file also sees the schema.
    with Database(path) as database:
        database.migrate()
        assert database.list_transmissions() == []


def test_transmission_roundtrip(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)

    row = db.get_transmission(tx.id)
    assert row is not None
    assert row["channel"] == "16"
    assert row["duration_ms"] == 4000
    assert row["started_at"] == "2026-07-30T12:00:00+00:00"
    assert row["est_snr_db"] == 15.0
    assert row["source"] == "file:test.wav"


def test_list_transmissions_newest_first(db: Database) -> None:
    first = make_transmission()
    second = make_transmission(started_at=datetime(2026, 7, 30, 13, 0, 0, tzinfo=UTC))
    db.insert_transmission(first)
    db.insert_transmission(second)

    rows = db.list_transmissions()
    assert [r["id"] for r in rows] == [second.id, first.id]


def test_naive_datetime_rejected(db: Database) -> None:
    # A naive datetime means some stage produced local time; storing it
    # would silently corrupt the timeline.
    tx = make_transmission(started_at=datetime(2026, 7, 30, 12, 0, 0))  # noqa: DTZ001
    with pytest.raises(ValueError, match="naive datetime"):
        db.insert_transmission(tx)


def test_duplicate_transmission_id_rejected(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_transmission(tx)


def test_transcript_is_append_only(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    transcript_id = db.insert_transcript(
        Transcript(transmission_id=tx.id, engine="test:none", text="mayday mayday")
    )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute(
            "UPDATE transcript SET text = 'edited' WHERE id = ?", (transcript_id,)
        )


def test_archived_transmission_is_append_only(db: Database) -> None:
    tx = make_transmission(audio_path="data/audio/x.opus", audio_sha256="ab" * 32)
    db.insert_transmission(tx)

    # Filling in archive info on an unarchived row is the one allowed update;
    # touching an archived row is not.
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute(
            "UPDATE transmission SET channel = '22' WHERE id = ?", (tx.id,)
        )


def test_transcript_requires_existing_transmission(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_transcript(
            Transcript(transmission_id="no-such-id", engine="test:none", text="x")
        )
