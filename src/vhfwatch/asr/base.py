"""Transcriber protocol and the result-shaping shared by all engines.

Engines are selected by config, never by platform sniffing
(docs/conventions.md §5): `mlx` on Apple Silicon for development,
`faster_whisper` everywhere for deployment. Both produce Whisper-style
segment dicts, so the merge logic lives here once and stays testable
without either heavy dependency installed.
"""

import math
from typing import Protocol, TypedDict

from vhfwatch.models import Transcript, Transmission, Word


class Transcriber(Protocol):
    name: str

    async def transcribe(self, tx: Transmission) -> Transcript: ...

    async def warmup(self) -> None: ...


class SegmentDict(TypedDict, total=False):
    """The subset of a Whisper segment both engines agree on."""

    text: str
    start: float
    end: float
    avg_logprob: float
    no_speech_prob: float
    words: list[dict[str, float | str]]


def merge_segments(
    segments: list[SegmentDict],
) -> tuple[str, list[Word], float | None, float | None]:
    """Collapse Whisper segments into (text, words, avg_logprob, no_speech_prob).

    Radio transmissions are seconds long, so usually one segment — but a
    long relayed broadcast can produce several. Quality stats are
    duration-weighted so a 1-second noise tail can't drag down (or launder)
    the score of a 20-second voiced body. These merged stats feed the
    post-ASR hallucination gate, which is why they must not be optimistic.
    """
    if not segments:
        return "", [], None, None

    text = " ".join(s.get("text", "").strip() for s in segments).strip()
    words: list[Word] = []
    for s in segments:
        for w in s.get("words", []) or []:
            words.append(
                Word(
                    text=str(w.get("word", w.get("text", ""))).strip(),
                    start_s=float(w.get("start", 0.0)),
                    end_s=float(w.get("end", 0.0)),
                    confidence=(
                        float(w["probability"]) if "probability" in w else None
                    ),
                )
            )

    # Each statistic carries its own weight total, so segments that didn't
    # report it are excluded from the mean rather than diluting it toward
    # zero. Diluting would make the score OPTIMISTIC (logprobs are negative),
    # which is the one direction a gate input must never drift.
    logprob_acc = logprob_dur = 0.0
    nospeech_acc = nospeech_dur = 0.0
    for s in segments:
        dur = max(float(s.get("end", 0.0)) - float(s.get("start", 0.0)), 1e-3)
        if _reported(s.get("avg_logprob")):
            logprob_acc += float(s["avg_logprob"]) * dur
            logprob_dur += dur
        if _reported(s.get("no_speech_prob")):
            nospeech_acc += float(s["no_speech_prob"]) * dur
            nospeech_dur += dur

    avg_logprob = logprob_acc / logprob_dur if logprob_dur else None
    no_speech_prob = nospeech_acc / nospeech_dur if nospeech_dur else None
    return text, words, avg_logprob, no_speech_prob


def _reported(value: object) -> bool:
    """True only for a real number the engine actually reported.

    mlx-whisper returns **NaN** for `avg_logprob` on many segments instead of
    omitting the key (4 of 5 on the first real-audio run, 2026-09-07). NaN
    propagates through the weighted mean, and every comparison against NaN is
    False — so the post-ASR logprob gate stopped gating without saying so.
    Treat non-finite as "not reported" and let the caller see None, which is
    honest and which the gate already handles.
    """
    return isinstance(value, int | float) and math.isfinite(value)
