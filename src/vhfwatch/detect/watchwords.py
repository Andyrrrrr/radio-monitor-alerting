"""Tier 1 detection: watchword matching robust to ASR mangling.

Literal matching misses real distress calls — "mayday" degrades to
"may day", "made a", "mated" (docs/architecture.md §5.5). So three passes
over normalized text, cheapest first:

1. exact/substring on the configured terms
2. phonetic: Metaphone code equality per word, guarded by a Jaro-Winkler
   similarity floor. The guard exists because Metaphone codes for short
   words are 2-3 characters and collide absurdly ("mud" == "mayday" == MT);
   requiring spelling proximity keeps "mated" while dropping "moody".
   Note: this is classic Metaphone — jellyfish does not actually ship
   Double Metaphone, whatever older notes say.
3. bounded fuzzy (rapidfuzz ratio) on multi-word phrases, against token
   windows of the same length.

The suppress list drops routine traffic that merely looks alarming ("radio
check") — unless a critical term also matched, because "mayday" beats any
amount of suppression. Suppressions are logged: they are discards, and
discards get reasons (docs/conventions.md §4).

Terms live in config/watchwords.toml, user-editable, grouped by severity.
This module is pure matching — severity escalation and the LLM tier live in
the detector orchestration.
"""

import string
import tomllib
from dataclasses import dataclass
from pathlib import Path

import jellyfish
import structlog
from rapidfuzz import fuzz

from vhfwatch.config import DetectConfig
from vhfwatch.models import Severity

logger = structlog.get_logger(__name__)

# Fallback for a fresh checkout where nobody has copied the example yet.
_EXAMPLE_PATH = Path("config/watchwords.example.toml")

_PUNCT_TABLE = str.maketrans({c: " " for c in string.punctuation})


@dataclass
class WatchwordHit:
    """One matched term. `kind` records which pass matched — fuzzy/phonetic
    hits are second-class citizens for severity (docs/architecture.md §5.5
    table: partial match only → WATCH)."""

    term: str
    severity: Severity
    kind: str  # "exact" | "phonetic" | "fuzzy"
    matched_text: str
    score: float  # 1.0 exact; similarity ratio otherwise


def normalize(text: str) -> str:
    """Lowercase, punctuation → spaces, collapsed whitespace."""
    return " ".join(text.lower().translate(_PUNCT_TABLE).split())


@dataclass
class _Term:
    text: str  # normalized
    severity: Severity
    tokens: list[str]
    codes: list[str]  # metaphone per token


class WatchwordMatcher:
    def __init__(
        self,
        cfg: DetectConfig,
        groups: dict[Severity, list[str]],
        suppress: list[str],
    ) -> None:
        self._cfg = cfg
        self._suppress = [normalize(t) for t in suppress]
        self._terms: list[_Term] = []
        for severity, terms in groups.items():
            for raw in terms:
                text = normalize(raw)
                tokens = text.split()
                self._terms.append(
                    _Term(
                        text=text,
                        severity=severity,
                        tokens=tokens,
                        codes=[jellyfish.metaphone(t) for t in tokens],
                    )
                )

    @classmethod
    def from_toml(
        cls, cfg: DetectConfig, path: Path | None = None
    ) -> "WatchwordMatcher":
        """Load terms from watchwords.toml.

        The default path may fall back to the committed example (fresh
        checkout); an explicitly configured path must exist — same loud-vs-
        lenient split as config loading.
        """
        if path is None:
            path = cfg.watchwords_path
            if not path.exists() and path == DetectConfig().watchwords_path:
                logger.info(
                    "watchwords.using_example",
                    missing=str(path),
                    fallback=str(_EXAMPLE_PATH),
                )
                path = _EXAMPLE_PATH
        if not path.exists():
            raise FileNotFoundError(f"watchwords file not found: {path}")

        with path.open("rb") as f:
            data = tomllib.load(f)
        groups = {
            Severity.CRITICAL: list(data.get("critical", {}).get("terms", [])),
            Severity.URGENT: list(data.get("urgent", {}).get("terms", [])),
            Severity.WATCH: list(data.get("watch", {}).get("terms", [])),
        }
        suppress = list(data.get("suppress", {}).get("terms", []))
        return cls(cfg, groups, suppress)

    def match(self, text: str) -> list[WatchwordHit]:
        """All watchword hits in a transcript, best pass per term."""
        norm = normalize(text)
        if not norm:
            return []
        tokens = norm.split()
        codes = [jellyfish.metaphone(t) for t in tokens]

        hits: list[WatchwordHit] = []
        for term in self._terms:
            hit = self._match_term(term, norm, tokens, codes)
            if hit is not None:
                hits.append(hit)

        if hits and self._is_suppressed(norm, hits):
            logger.info(
                "watchwords.suppressed",
                text=norm,
                would_have_matched=[h.term for h in hits],
            )
            return []
        return hits

    def _match_term(
        self, term: _Term, norm: str, tokens: list[str], codes: list[str]
    ) -> WatchwordHit | None:
        # Pass 1: substring on normalized text. Word-boundary padding so
        # "aground" doesn't fire inside "background".
        if f" {term.text} " in f" {norm} ":
            return WatchwordHit(term.text, term.severity, "exact", term.text, 1.0)

        n = len(term.tokens)
        if n > len(tokens):
            return None

        # Pass 2: phonetic. Codes may differ by one edit — "mated" (MTT) vs
        # "mayday" (MT) is the canonical case — with the similarity guard
        # doing the false-positive control. Best-scoring window wins.
        # Short-token terms sit this pass out: "cpr" (KPR) is one edit from
        # "copy" (KP), and "copy" is the most common word on a radio.
        if any(len(t) < self._cfg.phonetic_min_token_len for t in term.tokens):
            return self._match_fuzzy(term, tokens, n)
        term_code = " ".join(term.codes)
        best_phonetic: WatchwordHit | None = None
        for i in range(len(tokens) - n + 1):
            window_code = " ".join(codes[i : i + n])
            if jellyfish.levenshtein_distance(window_code, term_code) > 1:
                continue
            window = " ".join(tokens[i : i + n])
            similarity = jellyfish.jaro_winkler_similarity(window, term.text)
            if similarity < self._cfg.phonetic_min_similarity:
                continue
            if n > 1 and not self._every_token_similar(tokens[i : i + n], term.tokens):
                continue
            if best_phonetic is None or similarity > best_phonetic.score:
                best_phonetic = WatchwordHit(
                    term.text, term.severity, "phonetic", window, similarity
                )
        if best_phonetic is not None:
            return best_phonetic
        return self._match_fuzzy(term, tokens, n)

    def _every_token_similar(
        self, window_tokens: list[str], term_tokens: list[str]
    ) -> bool:
        """Each token must resemble its counterpart, not just the joined string.

        Jaro-Winkler over the whole phrase lets one exact token carry a
        garbage one: "going to" scored 0.915 against "going down" on the
        strength of "going" alone, and fired a CRITICAL watchword on real
        Ch 16 traffic (docs/decisions.md D20). Per token, "to" vs "down"
        fails and the phrase is rejected.
        """
        return all(
            jellyfish.jaro_winkler_similarity(w, t)
            >= self._cfg.phonetic_min_token_similarity
            for w, t in zip(window_tokens, term_tokens, strict=True)
        )

    def _match_fuzzy(
        self, term: _Term, tokens: list[str], n: int
    ) -> WatchwordHit | None:
        # Pass 3: bounded fuzzy, multi-word phrases only. Single words are
        # too easy to fuzzy-match at any usable threshold.
        if n >= 2:
            best_score = 0.0
            best_window = ""
            for i in range(len(tokens) - n + 1):
                window = " ".join(tokens[i : i + n])
                score = fuzz.ratio(window, term.text) / 100.0
                if score > best_score:
                    best_score = score
                    best_window = window
            if best_score >= self._cfg.fuzzy_min_ratio:
                return WatchwordHit(
                    term.text, term.severity, "fuzzy", best_window, best_score
                )
        return None

    def _is_suppressed(self, norm: str, hits: list[WatchwordHit]) -> bool:
        """Routine-traffic suppression — overridden by any exact critical hit."""
        has_exact_critical = any(
            h.severity is Severity.CRITICAL and h.kind == "exact" for h in hits
        )
        if has_exact_critical:
            return False
        padded = f" {norm} "
        return any(f" {s} " in padded for s in self._suppress)
