"""Watchword matcher: the Tier 1 detector (docs/architecture.md §5.5).

Built against the committed example term list so the tests also guard that
file's structure — if someone reshapes watchwords.example.toml, these fail
before a rig quietly stops matching anything.
"""

from pathlib import Path

import pytest

from vhfwatch.config import DetectConfig
from vhfwatch.detect.watchwords import WatchwordMatcher, normalize
from vhfwatch.models import Severity

EXAMPLE = Path(__file__).parent.parent / "config" / "watchwords.example.toml"


@pytest.fixture(scope="module")
def matcher() -> WatchwordMatcher:
    return WatchwordMatcher.from_toml(DetectConfig(), EXAMPLE)


def test_normalize() -> None:
    assert normalize("  MAYDAY, mayday!  This-is  Serenity. ") == (
        "mayday mayday this is serenity"
    )


def test_exact_match_through_punctuation_and_case(matcher: WatchwordMatcher) -> None:
    hits = matcher.match("MAYDAY! Mayday... this is vessel Serenity")
    terms = {h.term for h in hits}
    assert "mayday" in terms
    hit = next(h for h in hits if h.term == "mayday")
    assert hit.kind == "exact"
    assert hit.severity is Severity.CRITICAL


def test_split_keyword_matches(matcher: WatchwordMatcher) -> None:
    # "may day" is in the term list precisely because ASR splits the word.
    hits = matcher.match("may day may day this is serenity")
    assert any(h.term == "may day" and h.kind == "exact" for h in hits)


def test_phonetic_match_mated(matcher: WatchwordMatcher) -> None:
    # Classic ASR mangling: "mated" shares a Metaphone-adjacent shape and
    # passes the Jaro-Winkler guard.
    hits = matcher.match("mated mated this is fishing vessel gale runner")
    phonetic = [h for h in hits if h.kind == "phonetic"]
    assert any(h.term == "mayday" for h in phonetic)


def test_phonetic_guard_blocks_unrelated_collisions(matcher: WatchwordMatcher) -> None:
    # "mud" and "mayday" share Metaphone code MT; the similarity floor must
    # keep this from becoming a detection. False positives are the failure
    # mode this project bias against (docs/decisions.md D1).
    assert matcher.match("lots of mud on the ramp today") == []


def test_mangled_phrase_still_matches(matcher: WatchwordMatcher) -> None:
    # "taking on water" with one word mangled by ASR. Whether the phonetic
    # or fuzzy pass catches it is an implementation detail; catching it is not.
    hits = matcher.match("we are taking on watter three people aboard")
    assert any(h.term == "taking on water" and h.kind != "exact" for h in hits)


def test_fuzzy_pass_catches_what_phonetics_miss() -> None:
    # "steerage" vs "steering": Metaphone codes differ by more than one
    # edit, so only the windowed fuzzy pass can catch it.
    m = WatchwordMatcher(
        DetectConfig(), {Severity.WATCH: ["lost steering"]}, suppress=[]
    )
    hits = m.match("we have lost steerage making for the breakwater")
    assert any(h.term == "lost steering" and h.kind == "fuzzy" for h in hits)


def test_no_substring_matches_inside_words(matcher: WatchwordMatcher) -> None:
    # "aground" must not fire inside "background", "cpr" not inside a word.
    assert matcher.match("checking the background noise levels") == []


def test_clean_traffic_matches_nothing(matcher: WatchwordMatcher) -> None:
    assert matcher.match("switching to channel six eight for our position report") == []


def test_radio_check_suppressed(matcher: WatchwordMatcher) -> None:
    # "need help" would be a WATCH hit, but "radio check" marks it routine.
    assert (
        matcher.match("radio check radio check do you need help with the volume") == []
    )


def test_mayday_overrides_suppression(matcher: WatchwordMatcher) -> None:
    # An exact critical term beats any suppress term in the same transcript.
    hits = matcher.match("this is not a radio check mayday mayday mayday")
    assert any(h.term == "mayday" for h in hits)


def test_multiple_severities_reported(matcher: WatchwordMatcher) -> None:
    hits = matcher.match("mayday we are taking on water and abandoning ship")
    severities = {h.severity for h in hits}
    assert Severity.CRITICAL in severities
    assert Severity.URGENT in severities


def test_copy_that_does_not_phonetic_match_cpr(matcher: WatchwordMatcher) -> None:
    # Regression: "copy" (KP) is one Metaphone edit from "cpr" (KPR) and is
    # the most common word on a radio. Short-token terms must skip the
    # phonetic pass or every "copy that" becomes a WATCH detection.
    assert matcher.match("copy that harbor control") == []


def test_explicit_missing_path_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        WatchwordMatcher.from_toml(DetectConfig(), Path("no/such/file.toml"))
