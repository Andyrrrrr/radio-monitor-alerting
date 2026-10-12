"""Position extraction: coordinates spoken or written in a transcript → a map link.

Spoken coordinates look nothing like "48°44'45.2"N". Over the radio it is
"four eight degrees four four decimal two minutes north, one two two degrees
three four decimal three minutes west", and the transcriber renders that
anywhere from `48 degrees 44.2 minutes north` to words to garbage — on 2026-09-07
"one two two degrees" came back as "one, two, two screens". So this module does
two things in order: turn number *words* into digits, then match coordinate
*shapes* on the result.

**A wrong pin that looks confident is worse than no pin**, and numbers are the
weakest thing the transcriber produces. Every choice below leans toward finding
fewer positions rather than inventing them (AGENTS.md: missed detections are
acceptable, silent or confident errors are not):

- BOTH a latitude and a longitude must be present, each ending in a hemisphere
  (north/south, east/west). A lone "48 degrees north" is not a position.
- Ranges are checked (latitude <= 90, longitude <= 180, minutes and seconds < 60).
- A number with no degree sign, no minutes and no decimals is not a coordinate,
  so "channel 16 north" cannot become one.
- The result is always presented as *read from the transcript*, with the parsed
  values shown, so it can be checked against the recording beside it.

Pure functions, no I/O, no network: it works with the network unplugged
(AGENTS.md constraint 5) and does not depend on the LLM tier.
"""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

_DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "niner": "9",  # radio pronunciation
}
_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _clean(token: str) -> str:
    return token.strip(".!?:")


def _below_hundred(tokens: list[str], i: int) -> tuple[int | None, int]:
    """Read "forty eight", "nineteen", "forty" or a single digit word."""
    if i >= len(tokens):
        return None, 0
    word = _clean(tokens[i])
    if word in _TEENS:
        return _TEENS[word], 1
    if word in _TENS:
        value = _TENS[word]
        if i + 1 < len(tokens) and _clean(tokens[i + 1]) in _DIGIT_WORDS:
            return value + int(_DIGIT_WORDS[_clean(tokens[i + 1])]), 2
        return value, 1
    if word in _DIGIT_WORDS:
        return int(_DIGIT_WORDS[word]), 1
    return None, 0


def _read_number(tokens: list[str], i: int) -> tuple[str | None, int]:
    """A number phrase starting at tokens[i] as digits, plus tokens consumed.

    Handles digit-by-digit speech ("one two two" -> "122"), compound tens
    ("forty eight" -> "48") and "one hundred twenty two". Digit-by-digit is the
    normal way positions are read over a radio, so it is the primary case.
    """
    word = _clean(tokens[i])
    if word in _DIGIT_WORDS:
        if i + 1 < len(tokens) and _clean(tokens[i + 1]) == "hundred":
            hundreds = int(_DIGIT_WORDS[word]) * 100
            rest, used = _below_hundred(tokens, i + 2)
            return str(hundreds + (rest or 0)), 2 + used
        digits = ""
        j = i
        while j < len(tokens) and _clean(tokens[j]) in _DIGIT_WORDS:
            digits += _DIGIT_WORDS[_clean(tokens[j])]
            j += 1
        return digits, j - i
    if word in _TEENS or word in _TENS:
        value, used = _below_hundred(tokens, i)
        if value is not None:
            return str(value), used
    return None, 0


def digitize(text: str) -> str:
    """Lower-case the text and turn number words into digits.

    "four four decimal two minutes" -> "44.2 minutes". Hyphens and commas are
    treated as spaces ("forty-eight", "one, two, two"); everything else — including
    the symbols in "48°44'45.2"N" — is left alone.
    """
    # Hyphens only split WORDS ("forty-eight"). A hyphen before a digit is a minus
    # sign, and losing it would move "-122.5709" from the Pacific Northwest to
    # Siberia: a confidently wrong pin, which is the one failure to avoid.
    lowered = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text.lower())
    tokens = lowered.replace(",", " ").replace(";", " ").split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        number, used = _read_number(tokens, i)
        if number is not None:
            out.append(number)
            i += used
        else:
            out.append(tokens[i])
            i += 1
    joined = " ".join(out)
    # "44 decimal 2" / "44 point 2" -> "44.2"
    return re.sub(r"(\d+)\s+(?:point|decimal)\s+(\d+)", r"\1.\2", joined)


# -- coordinate shapes ---------------------------------------------------------

_NUM = r"\d+(?:\.\d+)?"
_DEG = r"(?:°|º|degrees?|deg)"
_MIN = r"(?:'|′|’|minutes?|mins?)"
_SEC = r"(?:\"|″|”|''|seconds?|secs?)"


def _component(prefix: str, hemisphere: str, max_degree_digits: int) -> str:
    """One axis: degrees, optional minutes, optional seconds, then a hemisphere."""
    p = prefix
    return (
        rf"(?P<{p}d>\d{{1,{max_degree_digits}}}(?:\.\d+)?)\s*(?P<{p}du>{_DEG})?\s*"
        rf"(?:(?P<{p}m>{_NUM})\s*(?:{_MIN})?\s*)?"
        rf"(?:(?P<{p}s>{_NUM})\s*(?:{_SEC})?\s*)?"
        rf"(?P<{p}h>{hemisphere})\b"
    )


_SEPARATOR = r"[\s,;:]*(?:and\s+)?(?:longitude\s+)?"
_PAIR = re.compile(
    _component("lat", "north|south|n|s", 2)
    + _SEPARATOR
    + _component("lon", "east|west|e|w", 3)
)
# "48.7459, -122.5709" — a signed decimal pair, written not spoken, so it needs
# enough decimal places that it cannot be anything else.
_SIGNED_PAIR = re.compile(
    r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*,?\s*(-?\d{1,3}\.\d{3,})(?![\d.])"
)


@dataclass(frozen=True)
class Position:
    lat: float  # decimal degrees, north positive
    lon: float  # decimal degrees, east positive
    read_from: str  # the digitized text it was parsed from, for the log


def _axis(m: re.Match[str], prefix: str, limit: int) -> float | None:
    p = prefix
    degrees_text = m[f"{p}d"]
    minutes = float(m[f"{p}m"]) if m[f"{p}m"] else 0.0
    seconds = float(m[f"{p}s"]) if m[f"{p}s"] else 0.0
    if minutes >= 60 or seconds >= 60:
        return None
    # Something must say these numbers are a coordinate, or "channel 16 north"
    # qualifies: a degree sign or word, minutes, or decimal degrees.
    if not (m[f"{p}du"] or m[f"{p}m"] or "." in degrees_text):
        return None
    if "." in degrees_text and (m[f"{p}m"] or m[f"{p}s"]):
        return None  # decimal degrees AND minutes is inconsistent
    value = float(degrees_text) + minutes / 60.0 + seconds / 3600.0
    if value > limit:
        return None
    return -value if m[f"{p}h"][0] in "sw" else value


def find_positions(text: str) -> list[Position]:
    """Every position in a transcript; usually none, rarely more than one."""
    normalized = digitize(text)
    found: list[Position] = []
    for m in _PAIR.finditer(normalized):
        lat = _axis(m, "lat", 90)
        lon = _axis(m, "lon", 180)
        if lat is None or lon is None:
            logger.info(
                "position.rejected", read_from=m.group(0), reason="out of range"
            )
            continue
        found.append(Position(lat, lon, m.group(0)))
    for m in _SIGNED_PAIR.finditer(normalized):
        lat, lon = float(m[1]), float(m[2])
        if abs(lat) <= 90 and abs(lon) <= 180:
            found.append(Position(lat, lon, m.group(0)))
    unique: list[Position] = []
    for position in found:
        if not any(
            abs(position.lat - u.lat) < 1e-4 and abs(position.lon - u.lon) < 1e-4
            for u in unique
        ):
            unique.append(position)
    return unique


def maps_url(lat: float, lon: float) -> str:
    """Google Maps' documented search URL: opens the app on a phone, the site elsewhere."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


def _in_area(position: Position, area: list[float]) -> bool:
    lat_min, lat_max, lon_min, lon_max = area
    return lat_min <= position.lat <= lat_max and lon_min <= position.lon <= lon_max


def format_position(position: Position, expected_area: list[float]) -> str:
    """The lines that go in a notification beside the transcript.

    Worded as "read from the transcript" on purpose: this is parsed from machine
    output that is wrong about numbers more often than anything else, and the
    recording sits right beside it to check against.
    """
    ns = "N" if position.lat >= 0 else "S"
    ew = "E" if position.lon >= 0 else "W"
    lines = [
        (
            f"📍 {abs(position.lat):.4f}°{ns} {abs(position.lon):.4f}°{ew} "
            "(read from the transcript, check it against the recording)"
        ),
    ]
    if len(expected_area) == 4 and not _in_area(position, expected_area):
        lines.append("⚠️ outside the usual area, so probably misheard")
    lines.append(maps_url(position.lat, position.lon))
    return "\n".join(lines)
