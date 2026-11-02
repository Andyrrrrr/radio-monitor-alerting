"""Detector orchestration: Tier 1 watchwords → Tier 2 LLM → Detection.

Implements the severity table from docs/architecture.md §5.5:

    CRITICAL  "mayday" matched (any tier) AND LLM confirms distress
    URGENT    pan-pan, USCG relay, or distress semantics without the keyword
    WATCH     partial/fuzzy match only, or LLM confidence below threshold
    ROUTINE   everything else

Two deliberate judgment calls beyond the table, both biased by D1 (fewer
false positives beats maximum recall; a missed detection has other paths):

- **LLM unavailable → the Tier 1 severity stands untouched.** An exact
  "mayday" must alert CRITICAL with the network unplugged (constraint 6).
- **LLM confidently says routine → hits demote to WATCH, never vanish.**
  "We are NOT taking on water" matches a watchword the LLM can correctly
  dismiss — but the detection row stays visible for corpus review rather
  than disappearing.
- **Out-of-area speakers cap at WATCH.** A call that names e.g. "Victoria
  Coast Guard" is too far for the fire boat to answer, so it cannot ring the
  phone. Like the LLM demotion it stays visible, never dropped; it only ever
  lowers severity.
- **Strict urgent gate (D35, opt-in via `strict_urgent`).** CRITICAL/URGENT
  survives only when an exact mayday/pan-pan watchword, a Coast Guard speaker
  phrase and an in-territory place name are ALL in the transcript. Anything
  else rates WATCH. This knowingly trades missed calls (a kayaker, a fishing
  boat) for fewer rings.
  One exception raises severity back: a Coast Guard mayday/pan-pan held for
  lacking a place becomes an "anchor" for `strict_window_s`; a later Coast
  Guard transmission that names an in-zone place escalates it. The Coast
  Guard often opens, pauses, then gives the place.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from vhfwatch.config import DetectConfig
from vhfwatch.detect.classify import Classifier, LLMAssessment
from vhfwatch.detect.places import PlaceMatcher
from vhfwatch.detect.watchwords import WatchwordHit, WatchwordMatcher, normalize
from vhfwatch.models import Detection, Severity, Transcript, new_id

logger = structlog.get_logger(__name__)

_RANK = {
    Severity.ROUTINE: 0,
    Severity.WATCH: 1,
    Severity.URGENT: 2,
    Severity.CRITICAL: 3,
}


def _max_severity(a: Severity, b: Severity) -> Severity:
    return a if _RANK[a] >= _RANK[b] else b


def _tier1_severity(hits: list[WatchwordHit]) -> Severity:
    """Watchword-only severity: exact hits keep their group's severity,
    phonetic/fuzzy hits only ever rate WATCH (§5.5 table, WATCH row)."""
    severity = Severity.ROUTINE
    for h in hits:
        contributed = h.severity if h.kind == "exact" else Severity.WATCH
        severity = _max_severity(severity, contributed)
    return severity


@dataclass
class _Anchor:
    """A Coast Guard mayday/pan-pan held at WATCH only for want of a place."""

    at: datetime
    severity: Severity
    confidence: float
    terms: list[str]
    transmission_id: str


class Detector:
    def __init__(
        self,
        cfg: DetectConfig,
        matcher: WatchwordMatcher,
        classifier: Classifier,
        places: PlaceMatcher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if cfg.strict_urgent and places is None:
            # Without place lists the gate would mute every alert silently.
            raise ValueError("strict_urgent needs a PlaceMatcher")
        self._places = places
        self._cfg = cfg
        self._matcher = matcher
        self._classifier = classifier
        self._out_of_area = [normalize(p) for p in cfg.out_of_area_phrases]
        self._call_terms = {normalize(t) for t in cfg.urgent_call_terms}
        self._speakers = [normalize(p) for p in cfg.urgent_speaker_phrases]
        self._window = timedelta(seconds=cfg.strict_window_s)
        self._anchors: list[_Anchor] = []
        # Same wall clock the correlator uses (Detection.created_at); injectable
        # so the window can be tested without sleeping.
        self._clock = clock or (lambda: datetime.now(UTC))

    def _strict_gate_failure(self, text: str, hits: list[WatchwordHit]) -> str | None:
        """Why this transcript may not ring under the strict gate, or None if
        it passes (a Coast Guard mayday/pan-pan naming an in-zone place)."""
        if not any(h.kind == "exact" and h.term in self._call_terms for h in hits):
            return "no_mayday_or_pan_pan"
        if not self._coast_guard_speaks(text):
            return "not_coast_guard"
        assert self._places is not None
        if not self._places.find(text).in_territory:
            return "no_in_territory_place"
        return None

    def _coast_guard_speaks(self, text: str) -> bool:
        padded = f" {normalize(text)} "
        return any(f" {p} " in padded for p in self._speakers)

    def _anchor_to_escalate(self, text: str) -> _Anchor | None:
        """The held pan-pan this transcript completes: a Coast Guard
        transmission, not from an out-of-area station, naming an in-zone place
        while an anchor is still inside the window. Highest severity wins."""
        if not self._anchors or self._out_of_area_phrase(text) is not None:
            return None
        if not self._coast_guard_speaks(text):
            return None
        assert self._places is not None
        if not self._places.find(text).in_territory:
            return None
        return max(self._anchors, key=lambda a: _RANK[a.severity])

    def _out_of_area_phrase(self, text: str) -> str | None:
        """The first configured out-of-area phrase in `text`, word-bounded
        (same padding as the watchword exact pass)."""
        padded = f" {normalize(text)} "
        for phrase in self._out_of_area:
            if phrase and f" {phrase} " in padded:
                return phrase
        return None

    async def detect(
        self, transcript: Transcript, context: list[str] | None = None
    ) -> Detection | None:
        """Run both tiers over one transcript; None means nothing notable.

        `context` is the preceding few transcript texts, oldest first,
        passed through to the LLM (a lone 4-second clip is often
        uninterpretable).
        """
        now = self._clock()
        hits = self._matcher.match(transcript.text)

        # Tier 2 runs on any Tier 1 hit, or on longer transcripts even with
        # no hit — "taking on water fast, three people aboard" contains no
        # watchword and is exactly what the second condition exists to catch.
        assessment: LLMAssessment | None = None
        word_count = len(transcript.text.split())
        if hits or word_count >= self._cfg.llm_min_words:
            assessment = await self._classifier.classify(transcript.text, context)

        severity, confidence = self._resolve(hits, assessment)

        out_of_area = self._out_of_area_phrase(transcript.text)
        if out_of_area is not None and _RANK[severity] > _RANK[Severity.WATCH]:
            logger.info(
                "detection.demoted_out_of_area",
                transmission_id=transcript.transmission_id,
                phrase=out_of_area,
                from_severity=severity.value,
            )
            severity = Severity.WATCH

        escalation: _Anchor | None = None
        if self._cfg.strict_urgent:
            self._anchors = [a for a in self._anchors if now - a.at <= self._window]
            if _RANK[severity] > _RANK[Severity.WATCH]:
                reason = self._strict_gate_failure(transcript.text, hits)
                if reason is not None:
                    assert self._places is not None
                    mentions = self._places.find(transcript.text)
                    logger.info(
                        "detection.demoted_strict_urgent",
                        transmission_id=transcript.transmission_id,
                        reason=reason,
                        from_severity=severity.value,
                        matched_terms=[h.term for h in hits],
                        out_of_territory=list(mentions.out_of_territory),
                        edge=list(mentions.edge),
                    )
                    if reason == "no_in_territory_place":
                        # A Coast Guard pan-pan waiting for its place.
                        self._anchors.append(
                            _Anchor(
                                at=now,
                                severity=severity,
                                confidence=confidence,
                                terms=[h.term for h in hits],
                                transmission_id=transcript.transmission_id,
                            )
                        )
                    severity = Severity.WATCH
            escalation = self._anchor_to_escalate(transcript.text)

        matched_terms = [h.term for h in hits]
        tier = "both" if hits and assessment else ("watchword" if hits else "llm")
        if escalation is not None and _RANK[escalation.severity] > _RANK[severity]:
            assert self._places is not None
            logger.info(
                "detection.window_escalation",
                transmission_id=transcript.transmission_id,
                anchor_transmission_id=escalation.transmission_id,
                anchor_age_s=round((now - escalation.at).total_seconds()),
                severity=escalation.severity.value,
                places=list(self._places.find(transcript.text).in_territory),
            )
            severity = escalation.severity
            confidence = max(confidence, escalation.confidence)
            matched_terms += [t for t in escalation.terms if t not in matched_terms]
            tier = "window"
            self._anchors = []  # one escalation per held pan-pan
        elif not hits and (
            assessment is None
            or assessment.severity == "routine"
            or severity is Severity.ROUTINE
        ):
            return None

        detection = Detection(
            id=new_id(),
            transmission_id=transcript.transmission_id,
            severity=severity,
            confidence=confidence,
            matched_terms=matched_terms,
            tier=tier,
            classifier_output=assessment.model_dump() if assessment else None,
            created_at=now,
        )
        logger.info(
            "detection.created",
            transmission_id=transcript.transmission_id,
            detection_id=detection.id,
            severity=severity.value,
            tier=tier,
            matched_terms=detection.matched_terms,
            confidence=round(confidence, 2),
            llm_available=assessment is not None,
        )
        return detection

    def _resolve(
        self, hits: list[WatchwordHit], assessment: LLMAssessment | None
    ) -> tuple[Severity, float]:
        tier1 = _tier1_severity(hits)
        best_hit_score = max((h.score for h in hits), default=0.0)

        if assessment is None:
            # LLM didn't run or failed: Tier 1 stands alone, at its own
            # severity — the alert path never depends on the LLM.
            return tier1, best_hit_score

        conf = assessment.confidence
        if conf < self._cfg.llm_min_confidence:
            # An unsure LLM neither confirms nor dismisses (§5.5: below
            # threshold → WATCH). ROUTINE only when nothing matched either.
            if hits or assessment.severity != "routine":
                return Severity.WATCH, conf
            return Severity.ROUTINE, conf

        critical_hit = any(h.severity is Severity.CRITICAL for h in hits)
        if assessment.severity == "critical":
            # CRITICAL needs the keyword AND the confirmation; distress
            # semantics without the keyword rates URGENT (§5.5 table).
            return (Severity.CRITICAL if critical_hit else Severity.URGENT), conf
        if assessment.severity == "urgent":
            return Severity.URGENT, conf
        if assessment.severity == "watch":
            return Severity.WATCH, conf
        # LLM confidently routine: demote hits to WATCH (visible, logged)
        # rather than dropping them — the demotion itself is reviewable.
        if hits:
            logger.info(
                "detection.demoted_by_llm",
                matched_terms=[h.term for h in hits],
                llm_confidence=round(conf, 2),
                reasoning=assessment.reasoning,
            )
            return Severity.WATCH, conf
        return Severity.ROUTINE, conf
