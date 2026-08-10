"""Store layer: migrations, roundtrips, and the append-only guarantees.

The append-only tests matter most here — they pin the D9 triggers so a
future schema change can't quietly drop them (docs/decisions.md D9).
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from vhfwatch.models import (
    Detection,
    Incident,
    Severity,
    Transcript,
    Transmission,
    new_id,
)
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


def make_detection(transmission_id: str, **overrides: object) -> Detection:
    d = Detection(
        id=new_id(),
        transmission_id=transmission_id,
        severity=Severity.CRITICAL,
        confidence=0.9,
        matched_terms=["mayday"],
        tier="watchword",
        classifier_output=None,
        created_at=datetime(2026, 7, 30, 12, 0, 5, tzinfo=UTC),
    )
    for key, value in overrides.items():
        setattr(d, key, value)
    return d


def make_incident(detection_ids: list[str], **overrides: object) -> Incident:
    opened = datetime(2026, 7, 30, 12, 0, 5, tzinfo=UTC)
    inc = Incident(
        id=new_id(),
        opened_at=opened,
        last_activity_at=opened,
        closed_at=None,
        status="open",
        severity=Severity.CRITICAL,
        channels=["16"],
        detection_ids=detection_ids,
        summary=None,
        confidence=0.9,
        alerted_at=None,
        acknowledged_at=None,
        acknowledged_by=None,
    )
    for key, value in overrides.items():
        setattr(inc, key, value)
    return inc


def test_detection_roundtrip(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d = make_detection(tx.id, classifier_output={"nature_of_emergency": "fire"})
    db.insert_detection(d)

    row = db.get_detection(d.id)
    assert row is not None
    assert row["severity"] == "critical"
    assert json.loads(row["matched_terms"]) == ["mayday"]
    assert json.loads(row["classifier_output"])["nature_of_emergency"] == "fire"


def test_incident_roundtrip_and_update(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d1 = make_detection(tx.id)
    d2 = make_detection(tx.id)
    db.insert_detection(d1)
    db.insert_detection(d2)

    inc = make_incident([d1.id], severity=Severity.WATCH)
    db.insert_incident(inc)

    # Incidents are the one mutable record: accrue a member, escalate, close.
    inc.detection_ids.append(d2.id)
    inc.severity = Severity.CRITICAL
    inc.status = "closed"
    inc.closed_at = inc.opened_at + timedelta(minutes=10)
    db.update_incident(inc)

    row = db.get_incident(inc.id)
    assert row is not None
    assert row["severity"] == "critical"
    assert row["status"] == "closed"
    members = db.incident_detections(inc.id)
    assert {m["id"] for m in members} == {d1.id, d2.id}


def test_open_incidents_listed_for_restart(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d = make_detection(tx.id)
    db.insert_detection(d)
    open_inc = make_incident([d.id])
    closed_inc = make_incident(
        [d.id], status="closed", closed_at=datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    )
    db.insert_incident(open_inc)
    db.insert_incident(closed_inc)

    assert [r["id"] for r in db.list_open_incidents()] == [open_inc.id]


def test_first_ack_wins(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d = make_detection(tx.id)
    db.insert_detection(d)
    inc = make_incident([d.id])
    db.insert_incident(inc)

    assert db.ack_incident(inc.id, "andy") is True
    # A second ack must not overwrite who actually responded.
    assert db.ack_incident(inc.id, "parker") is False
    row = db.get_incident(inc.id)
    assert row is not None
    assert row["acknowledged_by"] == "andy"
    assert db.ack_incident("no-such-id", "andy") is False


def test_annotations_append(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    db.insert_annotation("transmission", tx.id, "andy", "actually said 'radio check'")
    db.insert_annotation("transmission", tx.id, "parker", "agree")

    notes = db.list_annotations("transmission", tx.id)
    assert [n["author"] for n in notes] == ["andy", "parker"]


def test_alert_log_records_failures_too(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d = make_detection(tx.id)
    db.insert_detection(d)
    inc = make_incident([d.id])
    db.insert_incident(inc)

    db.insert_alert(inc.id, "pushover", is_update=False, ok=False, detail="timeout")
    row = db._conn.execute("SELECT * FROM alert").fetchone()
    assert row["ok"] == 0
    assert row["detail"] == "timeout"


def test_share_token_lookup_by_hash_only(db: Database) -> None:
    tx = make_transmission()
    db.insert_transmission(tx)
    d = make_detection(tx.id)
    db.insert_detection(d)
    inc = make_incident([d.id])
    db.insert_incident(inc)

    expires = datetime(2027, 7, 30, tzinfo=UTC)
    db.insert_share_token(inc.id, "ab" * 32, "andy", expires)
    found = db.get_share_token("ab" * 32)
    assert found is not None
    assert found["incident_id"] == inc.id
    assert db.get_share_token("cd" * 32) is None


def test_transmissions_after_cursor(db: Database) -> None:
    first = make_transmission()
    second = make_transmission()
    db.insert_transmission(first)
    db.insert_transmission(second)

    # ULIDs sort chronologically, so id order is insertion order here.
    older, newer = sorted([first.id, second.id])
    rows = db.transmissions_after(older)
    assert [r["id"] for r in rows] == [newer]
    assert db.last_transmission_at() is not None
