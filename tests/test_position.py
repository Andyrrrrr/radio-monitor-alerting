"""Position extraction: spoken or written coordinates -> a map link (D30).

The failures that matter here are WRONG pins, not missing ones: a confident link
to the wrong place is worse than none. So most of these tests are about what must
NOT parse, and about sign and range errors.
"""

import asyncio
from pathlib import Path

import pytest

from tests.synth import BASE
from tests.test_capture_watchdog import RecordingAudioChannel
from tests.test_pipeline import FakeTranscriber
from vhfwatch import pipeline
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.config import Settings
from vhfwatch.detect.position import (
    Position,
    digitize,
    find_positions,
    format_position,
    maps_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.wav"

# The operator's own example: 48°44'45.2"N 122°34'15.3"W
EXAMPLE_LAT = 48 + 44 / 60 + 45.2 / 3600
EXAMPLE_LON = -(122 + 34 / 60 + 15.3 / 3600)
DDM_LAT = 48 + 44.2 / 60
DDM_LON = -(122 + 34.3 / 60)


def one(text: str) -> Position:
    found = find_positions(text)
    assert len(found) == 1, f"expected one position in {text!r}, got {found}"
    return found[0]


@pytest.mark.parametrize(
    ("text", "lat", "lon"),
    [
        ("48°44'45.2\"N 122°34'15.3\"W", EXAMPLE_LAT, EXAMPLE_LON),  # the operator's
        ("48°44.2'N 122°34.3'W", DDM_LAT, DDM_LON),
        # What a transcriber actually writes for a spoken position:
        (
            (
                "48 degrees 44 minutes 45 seconds north, 122 degrees 34 minutes "
                "15 seconds west"
            ),
            48 + 44 / 60 + 45 / 3600,
            -(122 + 34 / 60 + 15 / 3600),
        ),
        (
            "we are at 48 degrees 44.2 minutes north 122 degrees 34.3 minutes west",
            DDM_LAT,
            DDM_LON,
        ),
        # Digit by digit, the normal way a position is read over a radio:
        (
            (
                "four eight degrees four four decimal two minutes north one two "
                "two degrees three four decimal three minutes west"
            ),
            DDM_LAT,
            DDM_LON,
        ),
        (
            (
                "forty-eight degrees forty-four minutes north, one hundred "
                "twenty-two degrees thirty-four minutes west"
            ),
            48 + 44 / 60,
            -(122 + 34 / 60),
        ),
        # The 2026-09-07 failure shape: digits split by commas.
        (
            (
                "four eight degrees four four minutes north, one, two, two "
                "degrees three four minutes west"
            ),
            48 + 44 / 60,
            -(122 + 34 / 60),
        ),
        ("our position 48 44.2 north 122 34.3 west over", DDM_LAT, DDM_LON),
        ("48.7459 N 122.5709 W", 48.7459, -122.5709),
        (
            "33 degrees 51 minutes south 151 degrees 12 minutes east",
            -(33 + 51 / 60),
            151 + 12 / 60,
        ),
    ],
)
def test_formats_that_must_parse(text: str, lat: float, lon: float) -> None:
    position = one(text)
    assert position.lat == pytest.approx(lat, abs=1e-4)
    assert position.lon == pytest.approx(lon, abs=1e-4)


def test_signed_decimal_pair_keeps_its_minus_sign() -> None:
    """Found by testing: the first version turned every hyphen into a space, which
    deleted the minus and moved -122.5709 from off Bellingham to Siberia. A
    confidently wrong pin is the one failure this feature must not have."""
    position = one("48.7459, -122.5709")
    assert position.lon == pytest.approx(-122.5709)
    southern = one("position -33.8500, 151.2000 over")
    assert southern.lat == pytest.approx(-33.85)


@pytest.mark.parametrize(
    "text",
    [
        "we are at 48 degrees 44 minutes north",  # latitude alone is not a position
        "switch to channel 16 north of the island",
        "buoy 14 north and light 12 west of the point",
        "95 degrees 10 minutes north 122 degrees 34 minutes west",  # latitude > 90
        "48 degrees 75 minutes north 122 degrees 34 minutes west",  # minutes >= 60
        "just north of Mendoza Island, coming around to Bellingham channel",
        "Roger, Captain. We're off on 13th, channel one three.",
        "",
    ],
)
def test_things_that_must_not_parse(text: str) -> None:
    assert find_positions(text) == []


def test_real_transcripts_contain_no_positions() -> None:
    """Every real transcript captured so far. Nobody has spoken coordinates yet, so
    a hit here is a false positive, and the 'around Mendoza' / 'channel 26' /
    'ATB, Culsor Line 26' style speech is exactly what would produce one."""
    real = [
        (
            "Hey, good afternoon, we're at the ATB on your port side there, just "
            "north of Mendoza Island. We're going to be coming around Mendoza and "
            "head down to Bellingham channel. We'll keep you guys on the starboard."
        ),
        "Sealboat U of mine, north of Andobe Island, ATB, Culsor Line 26.",
        "Blue skies, are you just still boat just south of B.D. Rocks?",
        "We're off on 13th, Captain.",
        "Sir Muggis, this is United States Coast Guard on channel 26, over.",
        "Radio check on channel 16. Radio check.",
        "For budget, this is Coast Guard on channel 16. We just gave you a call.",
    ]
    for text in real:
        assert find_positions(text) == [], text


def test_digitize_handles_the_ways_numbers_are_spoken() -> None:
    assert digitize("one two two") == "122"
    assert digitize("forty eight") == "48"
    assert digitize("forty-eight") == "48"
    assert digitize("one hundred twenty two") == "122"
    assert digitize("four four decimal two") == "44.2"
    assert digitize("niner") == "9"
    assert digitize("-122.5709") == "-122.5709"  # a minus is not a word separator


def test_maps_url_is_googles_documented_search_url() -> None:
    assert maps_url(48.745889, -122.570917) == (
        "https://www.google.com/maps/search/?api=1&query=48.745889,-122.570917"
    )


def test_format_says_where_it_came_from_and_flags_the_unlikely() -> None:
    pin = Position(48.7459, -122.5709, "")
    text = format_position(pin, [])
    assert "check it against the recording" in text
    assert "google.com/maps" in text
    assert "outside" not in text  # no area configured, no opinion

    inside = format_position(pin, [46.0, 52.0, -130.0, -120.0])
    assert "outside" not in inside

    # "122 degrees EAST" is a mishearing of west; flag it rather than hide it.
    far = format_position(Position(48.7459, 122.5709, ""), [46.0, 52.0, -130.0, -120.0])
    assert "probably misheard" in far


def _run(
    tmp_path: Path, texts: list[str], *, enabled: bool = True
) -> RecordingAudioChannel:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.alerting.notify_every_transmission = True
    cfg.alerting.channels_transmission = ["telegram"]
    cfg.position.enabled = enabled
    channel = RecordingAudioChannel()
    router = AlertRouter(cfg.alerting, {"telegram": channel})
    source = FileAudioSource(FIXTURE, start_at=BASE)
    asyncio.run(pipeline.run(cfg, source, FakeTranscriber(texts), router))
    return channel


def test_the_map_link_reaches_the_notification_before_the_transcript(
    tmp_path: Path,
) -> None:
    said = (
        "Mayday vessel Serenity, our position is 48 degrees 44 minutes north, "
        "122 degrees 34 minutes west, taking on water"
    )
    channel = _run(tmp_path, [said, "radio check"])
    captions = [c for _, c in channel.audio]
    with_pin = next(c for c in captions if "Serenity" in c)
    assert "google.com/maps/search" in with_pin
    # Before the transcript, so a caption cut at 1024 characters keeps the link.
    assert with_pin.index("google.com/maps") < with_pin.index("Serenity")
    # And an ordinary transmission gets none.
    plain = next(c for c in captions if "Serenity" not in c)
    assert "google.com/maps" not in plain


def test_disabled_means_no_link(tmp_path: Path) -> None:
    said = "position 48 degrees 44 minutes north 122 degrees 34 minutes west"
    channel = _run(tmp_path, [said, said], enabled=False)
    assert all("google.com/maps" not in c for _, c in channel.audio)
