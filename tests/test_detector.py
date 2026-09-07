"""Detector orchestration: the severity table from docs/architecture.md §5.5,
including the two degradation judgment calls (LLM down → Tier 1 stands;
LLM confidently routine → demote to WATCH, never drop)."""

import asyncio
from pathlib import Path

import pytest

from tests.test_classify import make_assessment
from vhfwatch.config import DetectConfig
from vhfwatch.detect.classify import Classifier, LLMAssessment
from vhfwatch.detect.detector import Detector
from vhfwatch.detect.watchwords import WatchwordMatcher
from vhfwatch.models import Severity, Transcript, new_id

EXAMPLE = Path(__file__).parent.parent / "config" / "watchwords.example.toml"


class FakeClassifier(Classifier):
    """Returns a canned assessment; None simulates LLM-unavailable."""

    def __init__(self, result: LLMAssessment | None):
        self._result = result
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._result is not None

    async def classify(
        self, text: str, context: list[str] | None = None
    ) -> LLMAssessment | None:
        self.calls.append(text)
        return self._result


def make_transcript(text: str) -> Transcript:
    return Transcript(transmission_id=new_id(), engine="test:none", text=text)


def run_detect(
    text: str, result: LLMAssessment | None, cfg: DetectConfig | None = None
):
    cfg = cfg or DetectConfig()
    matcher = WatchwordMatcher.from_toml(cfg, EXAMPLE)
    detector = Detector(cfg, matcher, FakeClassifier(result))
    return asyncio.run(detector.detect(make_transcript(text)))


def test_mayday_with_llm_confirmation_is_critical() -> None:
    d = run_detect(
        "mayday mayday mayday this is serenity",
        make_assessment(severity="critical", confidence=0.95),
    )
    assert d is not None
    assert d.severity is Severity.CRITICAL
    assert d.tier == "both"
    assert "mayday" in d.matched_terms


def test_mayday_with_llm_down_still_alerts_critical() -> None:
    # Constraint 6: the alert path never depends on the LLM. An exact
    # mayday with the network unplugged must still go out CRITICAL.
    d = run_detect("mayday mayday mayday this is serenity", None)
    assert d is not None
    assert d.severity is Severity.CRITICAL
    assert d.tier == "watchword"
    assert d.classifier_output is None


def test_distress_semantics_without_keyword_is_urgent() -> None:
    # "taking on water fast, three people aboard" style: no watchword,
    # LLM catches it — the reason Tier 2 runs on long transcripts at all.
    d = run_detect(
        "there is a lot of sea coming into the engine compartment three "
        "people on board we are five miles west of the harbor entrance",
        make_assessment(severity="critical", confidence=0.9),
    )
    assert d is not None
    assert d.severity is Severity.URGENT  # critical needs the keyword too
    assert d.tier == "llm"
    assert d.matched_terms == []


def test_low_llm_confidence_caps_at_watch() -> None:
    d = run_detect(
        "mayday mayday mayday this is serenity",
        make_assessment(severity="critical", confidence=0.3),
    )
    assert d is not None
    assert d.severity is Severity.WATCH


def test_llm_routine_demotes_hits_to_watch_not_gone() -> None:
    # "We are NOT taking on water" — the LLM can dismiss, but the row must
    # stay visible for corpus review, never silently dropped.
    d = run_detect(
        "confirming we are not taking on water all pumps working fine",
        make_assessment(severity="routine", confidence=0.95, nature_of_emergency=None),
    )
    assert d is not None
    assert d.severity is Severity.WATCH


def test_clean_traffic_yields_no_detection() -> None:
    d = run_detect(
        "harbor control this is water taxi seven requesting berth assignment",
        make_assessment(severity="routine", confidence=0.95, nature_of_emergency=None),
    )
    assert d is None


def test_short_clean_traffic_never_calls_llm() -> None:
    cfg = DetectConfig()
    matcher = WatchwordMatcher.from_toml(cfg, EXAMPLE)
    classifier = FakeClassifier(make_assessment())
    detector = Detector(cfg, matcher, classifier)
    d = asyncio.run(detector.detect(make_transcript("copy that")))
    assert d is None
    assert classifier.calls == []


def test_fuzzy_only_match_without_llm_is_watch() -> None:
    # Phonetic/fuzzy hits are second-class: WATCH, not the group severity.
    # "mayde" rather than "mated": the phonetic floor was raised to 0.85 on
    # 2026-09-07 and "mated" (0.760) no longer matches by design (D20). The
    # rule under test here is severity resolution, not that particular word.
    d = run_detect("mayde mayde this is gale runner", None)
    assert d is not None
    assert d.severity is Severity.WATCH


@pytest.mark.parametrize(
    "llm_severity,expected",
    [
        ("urgent", Severity.URGENT),
        ("watch", Severity.WATCH),
    ],
)
def test_llm_severity_mapping(llm_severity: str, expected: Severity) -> None:
    d = run_detect(
        "we have a medical emergency on board need assistance at the breakwater",
        make_assessment(severity=llm_severity, confidence=0.9),
    )
    assert d is not None
    assert d.severity is expected
