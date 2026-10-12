"""Watchword matcher: the Tier 1 detector (docs/architecture.md §5.5).

Built against the committed example term list so the tests also guard that
file's structure — if someone reshapes watchwords.example.toml, these fail
before a rig quietly stops matching anything.
"""

from pathlib import Path

import pytest
import structlog.testing

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


def test_mated_is_deliberately_no_longer_matched(matcher: WatchwordMatcher) -> None:
    # This test previously asserted the OPPOSITE, and the change is a
    # deliberate, measured trade — not a regression. "mated" scores 0.760
    # against "mayday", BELOW "my", "made" and "monday" at 0.800, so no
    # similarity floor keeps it without also admitting the most common words
    # on a radio. Measured against real Ch 16 traffic on 2026-09-07; see
    # docs/decisions.md D20. AGENTS.md settles the direction: missed
    # detections are acceptable, false positives are not.
    hits = matcher.match("mated mated this is fishing vessel gale runner")
    assert [h for h in hits if h.term == "mayday"] == []


def test_phonetic_pass_still_earns_its_place(matcher: WatchwordMatcher) -> None:
    # The raised floor must not make the phonetic pass dead weight: a closer
    # mangling still has to get through, or we should have deleted the pass.
    hits = matcher.match("mayde mayde this is vessel gale runner")
    assert any(h.term == "mayday" and h.kind == "phonetic" for h in hits)


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


# -- phonetic false positives found on real Ch 16 traffic, 2026-09-07 -------
#
# The first real transmission this project ever processed produced TWO
# CRITICAL watchword hits, neither of which appears in the transcript:
# "mayday" matched the word "my", and "going down" matched "going to".
# On a busy channel this fires constantly, which is the failure D12 warns
# about — users trained to silence the app. See docs/decisions.md D20.


@pytest.mark.parametrize(
    "phrase",
    [
        "my position is north",  # "my" -> mayday, score 0.80
        "copy that my friend",
        "made it in before dark",  # "made" -> mayday, 0.80
        "maybe later on channel nine",  # "maybe" -> mayday, 0.79
        "we are going to the marina",  # "going to" -> going down, 0.915
        "going to need a tow",
        "going to my slip",  # both false positives at once
    ],
)
def test_common_radio_words_do_not_fire(matcher: WatchwordMatcher, phrase: str) -> None:
    assert matcher.match(phrase) == []


@pytest.mark.parametrize(
    "transcript",
    [
        # Verbatim from the first real Ch 16 traffic captured on Parker's rig.
        "Paging calling US Coast Guard, this is US Coast Guard, go ahead over.",
        "Pushing the school, calling the Coast Guard. Yes, Coast Guard, go ahead, over.",
        "Have pleasure, boat. I just have to check on the channel.",
    ],
)
def test_real_captured_traffic_stays_quiet(
    matcher: WatchwordMatcher, transcript: str
) -> None:
    assert matcher.match(transcript) == []


@pytest.mark.parametrize(
    "phrase",
    [
        "mayday mayday mayday this is vessel serenity",
        "we are taking on water",
        "man overboard",
        "we are going down",
        "abandoning ship",
    ],
)
def test_real_distress_still_fires(matcher: WatchwordMatcher, phrase: str) -> None:
    # The guards must not be bought at the cost of the detections that matter.
    assert matcher.match(phrase) != []


def test_one_exact_token_cannot_carry_a_bad_one(matcher: WatchwordMatcher) -> None:
    # The multi-word mechanism specifically: Jaro-Winkler over the joined
    # string let "going" alone drag "going to" to 0.915 against "going down".
    assert [h for h in matcher.match("we are going to the fuel dock")] == []
    assert [h for h in matcher.match("we are going down") if h.term == "going down"]


# -- fuzzy_ignore_phrases: measured false positives on real Ch 16 traffic -----
# Every transcript below is REAL, from the 2026-10-12 station-rig session
# (docs/decisions.md D29). They are the regression guard for the ignore list.

REAL_FALSE_POSITIVES = [
    # "around" is one letter from "aground": phonetic hit at 0.957.
    (
        "Hey, good afternoon, we're at the ATB on your port side there, just north "
        "of Mendoza Island. We're going to be coming around Mendoza and head down "
        "to Bellingham channel. We'll keep you guys on the starboard."
    ),
    # "boat in the water" scores 0.824 against "dead in the water".
    (
        "Let's go on the thing of Cat Island. You guys take the boat to our cabin. "
        "I'm going to put your boat in the water. Are you to cook our dishes, over?"
    ),
    # "go down" scores 0.824 against the CRITICAL term "going down".
    "Japan, go down.",
]


@pytest.mark.parametrize("text", REAL_FALSE_POSITIVES)
def test_real_false_positives_no_longer_match(
    matcher: WatchwordMatcher, text: str
) -> None:
    assert matcher.match(text) == []


def test_ignore_list_never_hides_an_exact_match(matcher: WatchwordMatcher) -> None:
    """The whole safety argument: ignoring a LOOK-ALIKE window must never stop
    the real term firing. A real "aground" is an exact hit, and the exact pass
    runs before any fuzzy or phonetic logic."""
    for text, term in [
        ("we are aground on the rocks", "aground"),
        ("we are dead in the water", "dead in the water"),
        ("we are going down", "going down"),
    ]:
        hits = {h.term: h for h in matcher.match(text)}
        assert hits[term].kind == "exact"


def test_ignore_list_does_not_hide_a_mayday_that_mentions_around(
    matcher: WatchwordMatcher,
) -> None:
    """Why the ignore list is NOT the watchwords.toml `suppress` list, which
    drops every hit in a transcript containing a routine phrase."""
    hits = matcher.match("mayday mayday we are coming around the point")
    assert any(h.term == "mayday" and h.kind == "exact" for h in hits)


def test_garbled_versions_still_match(matcher: WatchwordMatcher) -> None:
    """The list is narrow on purpose. It names specific benign windows, not
    families, so an ASR mangling of a real term still gets through."""
    assert any(h.term == "dead in the water" for h in matcher.match("dad in the water"))
    assert any(h.term == "aground" for h in matcher.match("we are a ground"))


def test_ignored_windows_are_logged_with_a_reason(matcher: WatchwordMatcher) -> None:
    """Discards get reasons (docs/conventions.md §4). The log is also the review
    queue for the list: an entry that never appears is dead weight."""
    with structlog.testing.capture_logs() as logs:
        matcher.match("coming around the point")
    ignored = [e for e in logs if e["event"] == "watchwords.window_ignored"]
    assert len(ignored) == 1
    assert ignored[0]["window"] == "around"
    assert ignored[0]["term"] == "aground"


def test_ignore_list_is_configurable() -> None:
    off = WatchwordMatcher.from_toml(DetectConfig(fuzzy_ignore_phrases=[]), EXAMPLE)
    assert any(h.term == "aground" for h in off.match("coming around the point"))
