"""Incident correlation: many detections → one incident.

One emergency produces a dozen-plus transmissions over several minutes;
fifteen buzzes for one mayday trains people to silence the app, which is
worse than no system (docs/decisions.md D12). Correlation signals, most
reliable first (docs/architecture.md §5.6):

1. fuzzy vessel-name match (names get mangled differently each time)
2. time proximity while activity continues, on a channel the incident spans
3. channel migration — "switch to channel 22" extends the incident there
4. position proximity, to disambiguate between multiple candidates

Rules: incidents stay open and accrue; severity escalates and never
auto-de-escalates (the reverse requires a human); a quiet period closes.

The class is a pure state machine over the detection stream — time comes
from the detections themselves plus an explicit tick(now), never from the
wall clock, so tests are deterministic. Persistence is the caller's job,
driven by the returned CorrelationResult.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from rapidfuzz import fuzz

from vhfwatch.config import CorrelatorConfig
from vhfwatch.models import Detection, Incident, IncidentSummary, Severity, new_id

logger = structlog.get_logger(__name__)

_RANK = {
    Severity.ROUTINE: 0,
    Severity.WATCH: 1,
    Severity.URGENT: 2,
    Severity.CRITICAL: 3,
}

# "switch to channel 22", "channel two two" arrives as digits often enough;
# spelled-out numbers are a corpus-tuning follow-up, logged when missed.
_CHANNEL_RE = re.compile(r"\bchannel\s+(\d{1,2}\s?[a-bA-B]?)\b", re.IGNORECASE)

_EARTH_RADIUS_KM = 6371.0


def extract_channels(text: str) -> list[str]:
    """Channel numbers mentioned in a transcript ("switch to channel 22")."""
    return [m.replace(" ", "").lower() for m in _CHANNEL_RE.findall(text)]


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance. Good enough at harbor scale."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class DetectionContext:
    """What the correlator needs beyond the Detection row itself."""

    channel: str  # channel the transmission was heard on
    transcript_text: str
    summary: IncidentSummary | None  # from the classifier, when it ran


@dataclass
class CorrelationResult:
    incident: Incident
    is_new: bool  # opened now → alert
    escalated: bool  # severity rose → re-alert


class IncidentCorrelator:
    def __init__(self, cfg: CorrelatorConfig) -> None:
        self._cfg = cfg
        self._open: list[Incident] = []
        # vessel names seen per incident id — matching material, not record
        self._vessels: dict[str, list[str]] = {}

    def adopt(self, incidents: list[Incident]) -> None:
        """Re-adopt open incidents after a restart, so a process bounce in
        the middle of a real emergency doesn't split it in two."""
        for inc in incidents:
            self._open.append(inc)
            self._vessels[inc.id] = (
                [inc.summary.vessel_name]
                if inc.summary and inc.summary.vessel_name
                else []
            )
        if incidents:
            logger.info("correlator.adopted", count=len(incidents))

    @property
    def open_incidents(self) -> list[Incident]:
        return list(self._open)

    def add(self, detection: Detection, ctx: DetectionContext) -> CorrelationResult:
        now = detection.created_at
        mentioned = extract_channels(ctx.transcript_text)
        vessel = ctx.summary.vessel_name if ctx.summary else None

        candidate = self._find_candidate(now, ctx.channel, vessel, ctx.summary)
        if candidate is None:
            return self._open_incident(detection, ctx, mentioned)

        # Accrue onto the existing incident.
        candidate.detection_ids.append(detection.id)
        candidate.last_activity_at = now
        for ch in [ctx.channel, *mentioned]:
            if ch not in candidate.channels:
                # Channel migration: follow the traffic (§5.6 signal 3).
                candidate.channels.append(ch)
        if ctx.summary is not None:
            # Latest summary wins — later transmissions carry more facts —
            # but never replace facts with nothing.
            candidate.summary = ctx.summary
        if vessel and vessel not in self._vessels[candidate.id]:
            self._vessels[candidate.id].append(vessel)
        candidate.confidence = max(candidate.confidence, detection.confidence)

        escalated = False
        if _RANK[detection.severity] > _RANK[candidate.severity]:
            # Escalate and re-alert; de-escalation requires a human (D12).
            logger.info(
                "incident.escalated",
                incident_id=candidate.id,
                from_severity=candidate.severity.value,
                to_severity=detection.severity.value,
            )
            candidate.severity = detection.severity
            escalated = True

        logger.info(
            "incident.updated",
            incident_id=candidate.id,
            detection_id=detection.id,
            detections=len(candidate.detection_ids),
        )
        return CorrelationResult(candidate, is_new=False, escalated=escalated)

    def tick(self, now: datetime) -> list[Incident]:
        """Close incidents that went quiet or ran too long; returns them
        for persistence. Call periodically and on every detection."""
        closed: list[Incident] = []
        still_open: list[Incident] = []
        for inc in self._open:
            quiet = now - inc.last_activity_at > timedelta(
                seconds=self._cfg.quiet_period_s
            )
            over_max = now - inc.opened_at > timedelta(
                seconds=self._cfg.max_incident_duration_s
            )
            if quiet or over_max:
                inc.status = "closed"
                inc.closed_at = now
                closed.append(inc)
                self._vessels.pop(inc.id, None)
                logger.info(
                    "incident.closed",
                    incident_id=inc.id,
                    reason="quiet_period" if quiet else "max_duration",
                    detections=len(inc.detection_ids),
                )
            else:
                still_open.append(inc)
        self._open = still_open
        return closed

    # -- matching ------------------------------------------------------------

    def _find_candidate(
        self,
        now: datetime,
        channel: str,
        vessel: str | None,
        summary: IncidentSummary | None,
    ) -> Incident | None:
        active = [
            inc
            for inc in self._open
            if now - inc.last_activity_at <= timedelta(seconds=self._cfg.quiet_period_s)
        ]
        if not active:
            return None

        # Signal 1: vessel name, fuzzy — most reliable when present.
        if vessel:
            best: tuple[float, Incident] | None = None
            for inc in active:
                for known in self._vessels.get(inc.id, []):
                    ratio = fuzz.ratio(vessel.lower(), known.lower()) / 100.0
                    if ratio >= self._cfg.vessel_match_ratio and (
                        best is None or ratio > best[0]
                    ):
                        best = (ratio, inc)
            if best is not None:
                return best[1]
            # A confidently different vessel name on the same channel is a
            # different emergency — don't fall through to time proximity
            # when every active incident has a non-matching name.
            if all(self._vessels.get(inc.id) for inc in active):
                return None

        # Signal 2: time proximity on a shared channel.
        on_channel = [inc for inc in active if channel in inc.channels]
        if not on_channel:
            return None
        if len(on_channel) > 1 and summary and summary.latitude is not None:
            # Signal 4: position breaks ties between multiple candidates.
            for inc in on_channel:
                s = inc.summary
                if s and s.latitude is not None and s.longitude is not None:
                    assert summary.longitude is not None
                    if (
                        _km_between(
                            summary.latitude,
                            summary.longitude,
                            s.latitude,
                            s.longitude,
                        )
                        <= self._cfg.position_match_km
                    ):
                        return inc
        # Most recently active wins.
        return max(on_channel, key=lambda inc: inc.last_activity_at)

    def _open_incident(
        self, detection: Detection, ctx: DetectionContext, mentioned: list[str]
    ) -> CorrelationResult:
        channels = [ctx.channel] + [ch for ch in mentioned if ch != ctx.channel]
        incident = Incident(
            id=new_id(),
            opened_at=detection.created_at,
            last_activity_at=detection.created_at,
            closed_at=None,
            status="open",
            severity=detection.severity,
            channels=channels,
            detection_ids=[detection.id],
            summary=ctx.summary,
            confidence=detection.confidence,
            alerted_at=None,
            acknowledged_at=None,
            acknowledged_by=None,
        )
        self._open.append(incident)
        vessel = ctx.summary.vessel_name if ctx.summary else None
        self._vessels[incident.id] = [vessel] if vessel else []
        logger.info(
            "incident.opened",
            incident_id=incident.id,
            severity=incident.severity.value,
            detection_id=detection.id,
        )
        return CorrelationResult(incident, is_new=True, escalated=False)
