"""Detector orchestration: the severity table from docs/architecture.md §5.5,
including the two degradation judgment calls (LLM down → Tier 1 stands;
LLM confidently routine → demote to WATCH, never drop)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_classify import make_assessment
from vhfwatch.config import DetectConfig
from vhfwatch.detect.classify import Classifier, LLMAssessment
from vhfwatch.detect.detector import Detector
from vhfwatch.detect.places import PlaceMatcher
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


# -- out-of-area phrases cap severity at WATCH ---------------------------------


def test_victoria_coast_guard_pan_pan_is_capped_at_watch() -> None:
    # Real Ch 16 wording (2026-10-12): an exact pan-pan is URGENT on its own.
    d = run_detect(
        "Station calling, pan-pan, this is Victoria Coast Guard radio, "
        "do you require assistance?",
        None,
    )
    assert d is not None
    assert d.severity is Severity.WATCH
    assert "pan pan" in d.matched_terms or "pan-pan" in d.matched_terms


def test_victoria_coast_guard_mayday_is_capped_but_still_recorded() -> None:
    d = run_detect(
        "mayday mayday mayday this is Victoria Coast Guard radio",
        make_assessment(severity="critical", confidence=0.95),
    )
    assert d is not None  # demoted, never dropped
    assert d.severity is Severity.WATCH
    assert "mayday" in d.matched_terms


def test_victoria_traffic_is_capped_at_watch() -> None:
    d = run_detect("pan pan pan pan this is Victoria traffic Victoria traffic", None)
    assert d is not None
    assert d.severity is Severity.WATCH


def test_same_mayday_without_victoria_stays_critical() -> None:
    d = run_detect("mayday mayday mayday this is serenity", None)
    assert d is not None
    assert d.severity is Severity.CRITICAL


def test_a_vessel_named_victoria_is_not_demoted() -> None:
    # The phrase is "Victoria Coast Guard", not the word "Victoria": a boat
    # called Victoria in trouble nearby must still ring.
    d = run_detect("mayday mayday mayday this is the vessel Victoria", None)
    assert d is not None
    assert d.severity is Severity.CRITICAL


def test_empty_out_of_area_list_turns_the_rule_off() -> None:
    d = run_detect(
        "mayday mayday mayday this is Victoria Coast Guard radio",
        None,
        DetectConfig(out_of_area_phrases=[]),
    )
    assert d is not None
    assert d.severity is Severity.CRITICAL


# -- strict urgent gate (D35) ---------------------------------------------------

PLACES = Path(__file__).parent.parent / "config" / "places.example.toml"
STRICT = DetectConfig(strict_urgent=True)


def run_strict(text: str, result: LLMAssessment | None = None):
    matcher = WatchwordMatcher.from_toml(STRICT, EXAMPLE)
    places = PlaceMatcher.from_toml(STRICT, PLACES)
    detector = Detector(STRICT, matcher, FakeClassifier(result), places)
    return asyncio.run(detector.detect(make_transcript(text)))


def test_coast_guard_pan_pan_naming_an_in_zone_place_still_rings() -> None:
    d = run_strict(
        "Pan-pan, pan-pan, pan-pan, all stations, this is United States Coast "
        "Guard, a 32 foot vessel in the vicinity of Rosario Strait."
    )
    assert d is not None
    assert d.severity is Severity.URGENT


def test_coast_guard_mayday_naming_an_in_zone_place_stays_critical() -> None:
    d = run_strict(
        "Mayday, mayday, mayday, this is United States Coast Guard, vessel "
        "sinking near Legoe Bay.",
        make_assessment(severity="critical", confidence=0.95),
    )
    assert d is not None
    assert d.severity is Severity.CRITICAL


def test_coast_guard_pan_pan_without_a_place_rates_watch() -> None:
    # The common real case: the announcement opener, location said later.
    d = run_strict(
        "Pan-pan, pan-pan, pan-pan, all stations, this is the United States "
        "Coast Guard sector Puget Sound, break."
    )
    assert d is not None
    assert d.severity is Severity.WATCH


def test_coast_guard_pan_pan_naming_only_an_out_of_zone_place_rates_watch() -> None:
    d = run_strict(
        "Pan-pan, pan-pan, pan-pan, this is United States Coast Guard, vessel "
        "in Port Townsend Bay."
    )
    assert d is not None
    assert d.severity is Severity.WATCH


def test_a_vessels_own_mayday_in_the_zone_rates_watch() -> None:
    # The accepted cost: a kayaker or fishing boat is not a Coast Guard call.
    d = run_strict("Mayday mayday mayday this is Serenity, aground off Lummi Island")
    assert d is not None
    assert d.severity is Severity.WATCH


def test_urgent_without_the_mayday_or_pan_pan_word_rates_watch() -> None:
    d = run_strict("Coast Guard, man overboard near Orcas Island.")
    assert d is not None
    assert d.severity is Severity.WATCH


def test_victoria_still_caps_even_when_an_in_zone_place_is_named() -> None:
    d = run_strict(
        "Pan-pan, pan-pan, this is Victoria Coast Guard radio, vessel near "
        "Sucia Island."
    )
    assert d is not None
    assert d.severity is Severity.WATCH


def test_an_alias_spelling_counts_as_the_place() -> None:
    d = run_strict(
        "Pan-pan, pan-pan, pan-pan, this is United States Coast Guard, vessel in "
        "the vicinity of Fair Haven."
    )
    assert d is not None
    assert d.severity is Severity.URGENT


def test_watch_level_traffic_is_untouched_by_the_gate() -> None:
    d = run_strict("securite securite securite this is United States Coast Guard")
    assert d is not None
    assert d.severity is Severity.WATCH


def test_gate_is_off_by_default() -> None:
    d = run_detect("mayday mayday mayday this is serenity off Lummi Island", None)
    assert d is not None
    assert d.severity is Severity.CRITICAL


def test_strict_gate_without_place_lists_refuses_to_start() -> None:
    matcher = WatchwordMatcher.from_toml(STRICT, EXAMPLE)
    with pytest.raises(ValueError, match="PlaceMatcher"):
        Detector(STRICT, matcher, FakeClassifier(None))


# -- the window: a held Coast Guard pan-pan waits for its place -----------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_window_detector(window_s: float = 300.0) -> tuple[Detector, Clock]:
    cfg = DetectConfig(strict_urgent=True, strict_window_s=window_s)
    clock = Clock()
    detector = Detector(
        cfg,
        WatchwordMatcher.from_toml(cfg, EXAMPLE),
        FakeClassifier(None),
        PlaceMatcher.from_toml(cfg, PLACES),
        clock=clock,
    )
    return detector, clock


def feed(detector: Detector, text: str):
    return asyncio.run(detector.detect(make_transcript(text)))


OPENER = (
    "Pan-pan, pan-pan, pan-pan, all stations, this is United States Coast Guard "
    "sector Puget Sound, break."
)
CONTINUATION = (
    "United States Coast Guard, a 32 foot white vessel in the vicinity of "
    "Fairhaven, over."
)


def test_a_later_coast_guard_place_escalates_the_held_pan_pan() -> None:
    detector, clock = make_window_detector()
    first = feed(detector, OPENER)
    assert first is not None and first.severity is Severity.WATCH
    clock.advance(90)
    second = feed(detector, CONTINUATION)
    assert second is not None
    assert second.severity is Severity.URGENT
    assert second.tier == "window"
    assert "pan pan" in second.matched_terms  # the anchor's terms travel with it


def test_the_place_arriving_after_the_window_does_not_escalate() -> None:
    detector, clock = make_window_detector(window_s=300)
    feed(detector, OPENER)
    clock.advance(301)
    assert feed(detector, CONTINUATION) is None


def test_a_place_with_no_coast_guard_speaker_does_not_escalate() -> None:
    # Cross-talk guard: any vessel naming a harbour must not complete it.
    detector, clock = make_window_detector()
    feed(detector, OPENER)
    clock.advance(30)
    assert feed(detector, "we are inbound Squalicum Harbor to the fuel dock") is None


def test_a_coast_guard_transmission_naming_an_out_of_zone_place_does_not_escalate() -> (
    None
):
    detector, clock = make_window_detector()
    feed(detector, OPENER)
    clock.advance(30)
    assert (
        feed(detector, "United States Coast Guard, vessel in Port Townsend Bay") is None
    )


def test_a_victoria_transmission_cannot_complete_the_window() -> None:
    detector, clock = make_window_detector()
    feed(detector, OPENER)
    clock.advance(30)
    assert (
        feed(detector, "This is Victoria Coast Guard radio, vessel near Orcas Island")
        is None
    )


def test_a_vessels_own_mayday_never_becomes_an_anchor() -> None:
    detector, clock = make_window_detector()
    first = feed(detector, "Mayday mayday mayday this is Serenity, taking on water")
    assert first is not None and first.severity is Severity.WATCH
    clock.advance(30)
    assert feed(detector, CONTINUATION) is None


def test_one_held_pan_pan_escalates_once() -> None:
    detector, clock = make_window_detector()
    feed(detector, OPENER)
    clock.advance(60)
    assert feed(detector, CONTINUATION) is not None
    clock.advance(30)
    assert feed(detector, CONTINUATION) is None


def test_the_window_is_a_series_not_just_a_pair() -> None:
    detector, clock = make_window_detector()
    feed(detector, OPENER)
    clock.advance(100)
    assert feed(detector, "United States Coast Guard, stand by, break.") is None
    clock.advance(100)
    second = feed(detector, CONTINUATION)
    assert second is not None and second.severity is Severity.URGENT


def test_the_window_is_off_when_the_gate_is_off() -> None:
    cfg = DetectConfig()
    detector = Detector(
        cfg, WatchwordMatcher.from_toml(cfg, EXAMPLE), FakeClassifier(None)
    )
    first = feed(detector, OPENER)
    assert first is not None and first.severity is Severity.URGENT
