"""Web app: access control (loopback vs share token), the incident record,
and the three write paths (ack, feedback, annotations)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from tests.synth import write_wav16
from vhfwatch.config import Settings
from vhfwatch.models import (
    Detection,
    Incident,
    IncidentSummary,
    Severity,
    Transcript,
    Transmission,
    Word,
    new_id,
)
from vhfwatch.store import Database
from vhfwatch.web.app import create_app
from vhfwatch.web.tokens import mint_share_link

T0 = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.general.data_dir = tmp_path / "data"
    settings.general.data_dir.mkdir(parents=True)
    return settings


@pytest.fixture
def populated(cfg: Settings) -> tuple[Settings, str, str]:
    """A store holding one full incident; returns (cfg, incident_id, tx_id)."""
    audio_dir = cfg.general.data_dir / "audio"
    audio_dir.mkdir()
    tx_id = new_id()
    write_wav16(audio_dir / f"{tx_id}.wav", np.zeros(16000, dtype=np.float32))

    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        db.migrate()
        db.insert_transmission(
            Transmission(
                id=tx_id,
                channel="16",
                started_at=T0,
                ended_at=T0 + timedelta(seconds=4),
                duration_ms=4000,
                pcm=np.zeros(1, dtype=np.float32),
                sample_rate=16000,
                rms_dbfs=-20.0,
                peak_dbfs=-6.0,
                est_snr_db=15.0,
                audio_path=str(audio_dir / f"{tx_id}.wav"),
                audio_sha256="ab" * 32,
                source="test",
            )
        )
        db.insert_transcript(
            Transcript(
                transmission_id=tx_id,
                engine="test:none",
                text="mayday mayday this is serenity",
                words=[
                    Word("mayday", 0.0, 0.5, 0.9),
                    Word("mayday", 0.6, 1.1, 0.3),  # low-confidence
                    Word("this", 1.2, 1.4, 0.95),
                    Word("is", 1.4, 1.5, 0.95),
                    Word("serenity", 1.5, 2.2, 0.7),
                ],
            )
        )
        detection = Detection(
            id=new_id(),
            transmission_id=tx_id,
            severity=Severity.CRITICAL,
            confidence=0.9,
            matched_terms=["mayday"],
            tier="both",
            classifier_output=None,
            created_at=T0,
        )
        db.insert_detection(detection)
        incident_id = new_id()
        db.insert_incident(
            Incident(
                id=incident_id,
                opened_at=T0,
                last_activity_at=T0,
                closed_at=None,
                status="open",
                severity=Severity.CRITICAL,
                channels=["16"],
                detection_ids=[detection.id],
                summary=IncidentSummary(
                    nature_of_emergency="taking on water",
                    vessel_name="Serenity",
                    vessel_description=None,
                    position_as_stated="off the breakwater",
                    latitude=None,
                    longitude=None,
                    persons_aboard=3,
                    injuries_reported=None,
                    verbatim_quote="mayday mayday this is serenity",
                    reasoning="explicit mayday",
                ),
                confidence=0.9,
                alerted_at=T0,
                acknowledged_at=None,
                acknowledged_by=None,
            )
        )
    return cfg, incident_id, tx_id


def client_for(cfg: Settings, host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(cfg), client=(host, 51234), follow_redirects=False)


def test_index_and_incident_render(populated: tuple[Settings, str, str]) -> None:
    cfg, incident_id, _ = populated
    with client_for(cfg) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "Serenity" in index.text

        page = client.get(f"/incidents/{incident_id}")
        assert page.status_code == 200
        # Priority order: audio, transcript, summary, feedback all present.
        assert "audio" in page.text
        assert "mayday" in page.text
        assert "taking on water" in page.text
        assert "Not useful" in page.text
        assert "low-conf" in page.text  # honest uncertainty rendering


def test_remote_access_needs_token(populated: tuple[Settings, str, str]) -> None:
    cfg, incident_id, _ = populated
    with client_for(cfg, host="203.0.113.10") as client:
        assert client.get(f"/incidents/{incident_id}").status_code == 403

    # Mint the link the alert would carry, then use it remotely.
    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        link = mint_share_link(db, cfg.web, incident_id)
    assert link is not None
    token = link.split("token=")[1]
    with client_for(cfg, host="203.0.113.10") as client:
        assert client.get(f"/incidents/{incident_id}?token={token}").status_code == 200
        # The token is scoped: no free access to other pages with side doors.
        other = client.get("/incidents/no-such-incident?token=" + token)
        assert other.status_code == 403


def test_audio_served(populated: tuple[Settings, str, str]) -> None:
    cfg, _, tx_id = populated
    with client_for(cfg) as client:
        response = client.get(f"/transmissions/{tx_id}/audio")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/")


def test_ack_first_wins(populated: tuple[Settings, str, str]) -> None:
    cfg, incident_id, _ = populated
    with client_for(cfg) as client:
        response = client.post(
            f"/incidents/{incident_id}/ack", data={"by": "andy", "token": ""}
        )
        assert response.status_code == 303
        page = client.get(f"/incidents/{incident_id}")
        assert "Acknowledged by andy" in page.text


def test_feedback_becomes_annotation(populated: tuple[Settings, str, str]) -> None:
    cfg, incident_id, _ = populated
    with client_for(cfg) as client:
        client.post(
            f"/incidents/{incident_id}/feedback",
            data={"verdict": "not-useful", "by": "parker", "token": ""},
        )
    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        notes = db.list_annotations("incident", incident_id)
    assert any("not-useful" in n["body"] for n in notes)


def test_annotations_append(populated: tuple[Settings, str, str]) -> None:
    cfg, incident_id, _ = populated
    with client_for(cfg) as client:
        client.post(
            f"/incidents/{incident_id}/annotations",
            data={
                "author": "andy",
                "body": "vessel name is actually Serenity II",
                "token": "",
            },
        )
        page = client.get(f"/incidents/{incident_id}")
        assert "Serenity II" in page.text


def test_health_is_honest_about_scope(cfg: Settings) -> None:
    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        db.migrate()
    with client_for(cfg) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert payload["last_transmission_at"] is None
        assert "Phase 4" in payload["note"]
