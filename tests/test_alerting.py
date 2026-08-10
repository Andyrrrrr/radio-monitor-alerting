"""Alerting: the one-alert-per-incident contract, escalation re-alerts,
dedupe, channel-failure isolation, and the offline-complete body."""

import asyncio
from datetime import UTC, datetime, timedelta

from vhfwatch.alerting.base import format_body, format_title
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.config import AlertingConfig
from vhfwatch.models import AlertResult, Incident, IncidentSummary, Severity, new_id

T0 = datetime(2026, 8, 10, 19, 30, 0, tzinfo=UTC)


def make_incident(
    severity: Severity = Severity.CRITICAL, **overrides: object
) -> Incident:
    inc = Incident(
        id=new_id(),
        opened_at=T0,
        last_activity_at=T0,
        closed_at=None,
        status="open",
        severity=severity,
        channels=["16"],
        detection_ids=[new_id()],
        summary=IncidentSummary(
            nature_of_emergency="taking on water",
            vessel_name="Serenity",
            vessel_description="30ft white sloop",
            position_as_stated="two miles west of the jetty",
            latitude=None,
            longitude=None,
            persons_aboard=3,
            injuries_reported=None,
            verbatim_quote="mayday mayday mayday this is serenity",
            reasoning="explicit mayday",
        ),
        confidence=0.9,
        alerted_at=None,
        acknowledged_at=None,
        acknowledged_by=None,
    )
    for key, value in overrides.items():
        setattr(inc, key, value)
    return inc


class RecordingChannel:
    def __init__(self, name: str, ok: bool = True, crash: bool = False):
        self.name = name
        self._ok = ok
        self._crash = crash
        self.sent: list[tuple[str, bool]] = []

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        if self._crash:
            raise RuntimeError("boom")
        self.sent.append((incident.id, is_update))
        return AlertResult(channel=self.name, ok=self._ok)

    async def healthcheck(self) -> bool:
        return True


def make_router(**channels: RecordingChannel) -> AlertRouter:
    cfg = AlertingConfig(
        channels_critical=list(channels),
        channels_urgent=list(channels),
        channels_watch=[],
        channels_routine=[],
    )
    return AlertRouter(cfg, dict(channels))


def test_body_is_actionable_offline() -> None:
    # Constraint: assume the recipient may never load the web page —
    # severity, time, vessel, nature, position, quote all in the message.
    inc = make_incident()
    body = format_body(inc, "America/Los_Angeles", "http://example/incidents/x")
    assert "CRITICAL" in body
    assert "Serenity" in body
    assert "taking on water" in body
    assert "two miles west of the jetty" in body
    assert "mayday mayday mayday" in body
    assert "3" in body
    assert "http://example/incidents/x" in body
    assert "12:30" in body  # 19:30 UTC rendered in local (PDT) time


def test_body_without_summary_is_honest() -> None:
    inc = make_incident(summary=None)
    body = format_body(inc, "America/Los_Angeles", None)
    assert "no classifier summary" in body


def test_title_marks_updates() -> None:
    inc = make_incident()
    assert format_title(inc, is_update=False).startswith("VHF ALERT")
    assert format_title(inc, is_update=True).startswith("UPDATE")


def test_new_incident_alerts_once() -> None:
    ch = RecordingChannel("console")
    router = make_router(console=ch)
    inc = make_incident()

    results = asyncio.run(router.dispatch(inc, is_new=True, escalated=False))
    assert len(results) == 1
    assert inc.alerted_at is not None

    # Accrual without escalation: silence.
    inc.last_activity_at = T0 + timedelta(minutes=1)
    results = asyncio.run(router.dispatch(inc, is_new=False, escalated=False))
    assert results == []
    assert len(ch.sent) == 1


def test_escalation_re_alerts_as_update() -> None:
    ch = RecordingChannel("console")
    router = make_router(console=ch)
    inc = make_incident(severity=Severity.URGENT)
    asyncio.run(router.dispatch(inc, is_new=True, escalated=False))

    inc.severity = Severity.CRITICAL
    inc.last_activity_at = T0 + timedelta(minutes=1)
    results = asyncio.run(router.dispatch(inc, is_new=False, escalated=True))
    assert len(results) == 1
    assert ch.sent[-1] == (inc.id, True)  # is_update


def test_dedupe_window_blocks_same_severity_repeat() -> None:
    ch = RecordingChannel("console")
    router = make_router(console=ch)
    inc = make_incident()
    asyncio.run(router.dispatch(inc, is_new=True, escalated=False))
    # Same severity again within the window (e.g. replayed escalation).
    inc.last_activity_at = T0 + timedelta(seconds=30)
    results = asyncio.run(router.dispatch(inc, is_new=True, escalated=False))
    assert results == []
    assert len(ch.sent) == 1


def test_channel_crash_does_not_stop_others() -> None:
    bad = RecordingChannel("bad", crash=True)
    good = RecordingChannel("good")
    router = make_router(bad=bad, good=good)
    inc = make_incident()

    results = asyncio.run(router.dispatch(inc, is_new=True, escalated=False))
    assert len(results) == 2
    by_name = {r.channel: r for r in results}
    assert by_name["bad"].ok is False
    assert by_name["good"].ok is True
    assert len(good.sent) == 1


def test_watch_routes_to_no_channels_quietly() -> None:
    router = make_router(console=RecordingChannel("console"))
    inc = make_incident(severity=Severity.WATCH)
    results = asyncio.run(router.dispatch(inc, is_new=True, escalated=False))
    assert results == []
