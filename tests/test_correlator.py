"""Correlator: synthetic detection streams asserting expected grouping,
channel migration, escalation without de-escalation, and quiet-period close
(docs/architecture.md §7)."""

from datetime import UTC, datetime, timedelta

from vhfwatch.config import CorrelatorConfig
from vhfwatch.incidents.correlator import (
    DetectionContext,
    IncidentCorrelator,
    extract_channels,
)
from vhfwatch.models import Detection, IncidentSummary, Severity, new_id

T0 = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


def make_detection(at: datetime, severity: Severity = Severity.CRITICAL) -> Detection:
    return Detection(
        id=new_id(),
        transmission_id=new_id(),
        severity=severity,
        confidence=0.9,
        matched_terms=["mayday"],
        tier="both",
        classifier_output=None,
        created_at=at,
    )


def make_summary(
    vessel: str | None, lat: float | None = None, lon: float | None = None
) -> IncidentSummary:
    return IncidentSummary(
        nature_of_emergency="taking on water",
        vessel_name=vessel,
        vessel_description=None,
        position_as_stated=None,
        latitude=lat,
        longitude=lon,
        persons_aboard=None,
        injuries_reported=None,
        verbatim_quote="mayday",
        reasoning="test",
    )


def ctx(
    text: str = "mayday", vessel: str | None = None, channel: str = "16"
) -> DetectionContext:
    return DetectionContext(
        channel=channel,
        transcript_text=text,
        summary=make_summary(vessel) if vessel else None,
    )


def test_one_conversation_one_incident() -> None:
    # The exit-criterion case: a mayday exchange over several minutes
    # collapses into ONE incident, not fifteen.
    c = IncidentCorrelator(CorrelatorConfig())
    first = c.add(make_detection(T0), ctx(vessel="Serenity"))
    assert first.is_new

    for minutes in (1, 2, 4):
        r = c.add(
            make_detection(T0 + timedelta(minutes=minutes)),
            ctx(vessel="Serenity"),
        )
        assert r.is_new is False
        assert r.incident.id == first.incident.id
    assert len(first.incident.detection_ids) == 4


def test_mangled_vessel_names_still_group() -> None:
    c = IncidentCorrelator(CorrelatorConfig())
    a = c.add(make_detection(T0), ctx(vessel="Serenity"))
    b = c.add(make_detection(T0 + timedelta(minutes=1)), ctx(vessel="Serenty"))
    assert b.incident.id == a.incident.id


def test_different_vessels_get_different_incidents() -> None:
    c = IncidentCorrelator(CorrelatorConfig())
    a = c.add(make_detection(T0), ctx(vessel="Serenity"))
    b = c.add(make_detection(T0 + timedelta(minutes=1)), ctx(vessel="Gale Runner"))
    assert b.incident.id != a.incident.id


def test_time_proximity_groups_when_no_vessel_known() -> None:
    # LLM down → no vessel names; activity on the same channel within the
    # window is still one incident.
    c = IncidentCorrelator(CorrelatorConfig())
    a = c.add(make_detection(T0), ctx())
    b = c.add(make_detection(T0 + timedelta(minutes=2)), ctx())
    assert b.incident.id == a.incident.id


def test_channel_migration_followed() -> None:
    c = IncidentCorrelator(CorrelatorConfig())
    a = c.add(make_detection(T0), ctx(text="mayday switch to channel 22"))
    assert "22" in a.incident.channels
    # Later traffic heard on 22 joins the same incident.
    b = c.add(make_detection(T0 + timedelta(minutes=2)), ctx(channel="22"))
    assert b.incident.id == a.incident.id


def test_severity_escalates_and_never_de_escalates() -> None:
    c = IncidentCorrelator(CorrelatorConfig())
    a = c.add(make_detection(T0, Severity.WATCH), ctx())
    r = c.add(make_detection(T0 + timedelta(minutes=1), Severity.CRITICAL), ctx())
    assert r.escalated is True
    assert r.incident.severity is Severity.CRITICAL

    later = c.add(make_detection(T0 + timedelta(minutes=2), Severity.WATCH), ctx())
    assert later.escalated is False
    assert later.incident.severity is Severity.CRITICAL  # no auto-de-escalation
    assert a.incident.id == later.incident.id


def test_quiet_period_closes_incident() -> None:
    cfg = CorrelatorConfig(quiet_period_s=300)
    c = IncidentCorrelator(cfg)
    a = c.add(make_detection(T0), ctx())

    assert c.tick(T0 + timedelta(seconds=200)) == []
    closed = c.tick(T0 + timedelta(seconds=400))
    assert [i.id for i in closed] == [a.incident.id]
    assert closed[0].status == "closed"

    # New traffic after the close is a new incident.
    b = c.add(make_detection(T0 + timedelta(seconds=500)), ctx())
    assert b.is_new


def test_max_duration_closes_incident() -> None:
    cfg = CorrelatorConfig(quiet_period_s=300, max_incident_duration_s=600)
    c = IncidentCorrelator(cfg)
    c.add(make_detection(T0), ctx())
    # Keep it active past max duration, then tick.
    c.add(make_detection(T0 + timedelta(seconds=550)), ctx())
    closed = c.tick(T0 + timedelta(seconds=650))
    assert len(closed) == 1


def test_adopt_resumes_open_incident_after_restart() -> None:
    cfg = CorrelatorConfig()
    c1 = IncidentCorrelator(cfg)
    a = c1.add(make_detection(T0), ctx(vessel="Serenity"))

    c2 = IncidentCorrelator(cfg)
    c2.adopt([a.incident])
    r = c2.add(make_detection(T0 + timedelta(minutes=1)), ctx(vessel="Serenity"))
    assert r.is_new is False
    assert r.incident.id == a.incident.id


def test_extract_channels() -> None:
    assert extract_channels("switch to channel 22a for the tow") == ["22a"]
    assert extract_channels("this is a mayday") == []
    assert extract_channels("Channel 16 and channel 22") == ["16", "22"]
