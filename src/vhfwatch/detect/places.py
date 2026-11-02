"""Named places in a transcript, sorted into in / edge / out of the response zone.

Only the strict urgent gate uses this (docs/decisions.md D35). The lists live in
config/places.toml (falls back to the committed config/places.example.toml);
see that file's header for how they were built and what is deliberately left off
(`Puget Sound` and `Seattle` are Coast Guard units, not places).

Matching is whole-word on normalized text, the same rule as the watchword exact
pass, so "Bellingham" does not fire inside "Bellinghamster". Whisper misspells
names, so `[aliases]` maps spellings seen in real transcripts to the correct
name; an alias counts as the list its target is in.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import structlog

from vhfwatch.config import DetectConfig
from vhfwatch.detect.watchwords import normalize

logger = structlog.get_logger(__name__)

_EXAMPLE_PATH = Path("config/places.example.toml")

# When one spelling is in two lists, the zone wins: a name that also exists
# outside it can only ever stop a demotion, never cause one (D35).
_PRECEDENCE = ("out_of_territory", "edge", "in_territory")


@dataclass(frozen=True)
class PlaceMentions:
    """Correctly spelled place names found in one transcript."""

    in_territory: tuple[str, ...] = ()
    edge: tuple[str, ...] = ()
    out_of_territory: tuple[str, ...] = ()


def _flatten(section: object) -> list[str]:
    """Every string in a table of arrays (in_territory, out_of_territory) or in
    a table with one `names` array (edge)."""
    if not isinstance(section, dict):
        return []
    return [name for names in section.values() for name in names]


class PlaceMatcher:
    def __init__(
        self, lists: dict[str, list[str]], aliases: dict[str, list[str]]
    ) -> None:
        # normalized spelling -> (name as written in the file, list it is in)
        self._spellings: dict[str, tuple[str, str]] = {}
        for kind in _PRECEDENCE:
            for name in lists.get(kind, []):
                key = normalize(name)
                if key:
                    self._spellings[key] = (name, kind)
        for target, spellings in aliases.items():
            known = self._spellings.get(normalize(target))
            if known is None:
                # A typo here would silently never match; fail at startup.
                raise ValueError(f"places alias points at an unlisted name: {target!r}")
            for spelling in spellings:
                self._spellings[normalize(spelling)] = known

    @classmethod
    def from_toml(cls, cfg: DetectConfig, path: Path | None = None) -> "PlaceMatcher":
        """Load the place lists.

        The default path falls back to the committed example (fresh checkout
        or a host that has not copied it yet); an explicitly configured path
        must exist. A missing file raises: the strict gate treats "no place
        found" as "do not ring", so an empty list silently muting every alert
        is the one failure this loader must not allow.
        """
        if path is None:
            path = cfg.places_path
            if not path.exists() and path == DetectConfig().places_path:
                logger.info(
                    "places.using_example",
                    missing=str(path),
                    fallback=str(_EXAMPLE_PATH),
                )
                path = _EXAMPLE_PATH
        if not path.exists():
            raise FileNotFoundError(f"places file not found: {path}")

        with path.open("rb") as f:
            data = tomllib.load(f)
        lists = {kind: _flatten(data.get(kind)) for kind in _PRECEDENCE}
        if not lists["in_territory"]:
            raise ValueError(f"{path} has no in_territory names")
        return cls(lists, data.get("aliases", {}))

    def find(self, text: str) -> PlaceMentions:
        padded = f" {normalize(text)} "
        found: dict[str, set[str]] = {kind: set() for kind in _PRECEDENCE}
        for spelling, (name, kind) in self._spellings.items():
            if f" {spelling} " in padded:
                found[kind].add(name)
        return PlaceMentions(
            in_territory=tuple(sorted(found["in_territory"])),
            edge=tuple(sorted(found["edge"])),
            out_of_territory=tuple(sorted(found["out_of_territory"])),
        )
