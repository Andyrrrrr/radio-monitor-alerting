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
"""

from datetime import UTC, datetime

import structlog

from vhfwatch.config import DetectConfig
from vhfwatch.detect.classify import Classifier, LLMAssessment
from vhfwatch.detect.watchwords import WatchwordHit, WatchwordMatcher
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


class Detector:
    def __init__(
        self, cfg: DetectConfig, matcher: WatchwordMatcher, classifier: Classifier
    ) -> None:
        self._cfg = cfg
        self._matcher = matcher
        self._classifier = classifier

    async def detect(
        self, transcript: Transcript, context: list[str] | None = None
    ) -> Detection | None:
        """Run both tiers over one transcript; None means nothing notable.

        `context` is the preceding few transcript texts, oldest first,
        passed through to the LLM (a lone 4-second clip is often
        uninterpretable).
        """
        hits = self._matcher.match(transcript.text)

        # Tier 2 runs on any Tier 1 hit, or on longer transcripts even with
        # no hit — "taking on water fast, three people aboard" contains no
        # watchword and is exactly what the second condition exists to catch.
        assessment: LLMAssessment | None = None
        word_count = len(transcript.text.split())
        if hits or word_count >= self._cfg.llm_min_words:
            assessment = await self._classifier.classify(transcript.text, context)

        severity, confidence = self._resolve(hits, assessment)

        if not hits and (assessment is None or assessment.severity == "routine"):
            return None
        if severity is Severity.ROUTINE and not hits:
            return None

        tier = "both" if hits and assessment else ("watchword" if hits else "llm")
        detection = Detection(
            id=new_id(),
            transmission_id=transcript.transmission_id,
            severity=severity,
            confidence=confidence,
            matched_terms=[h.term for h in hits],
            tier=tier,
            classifier_output=assessment.model_dump() if assessment else None,
            created_at=datetime.now(UTC),
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
