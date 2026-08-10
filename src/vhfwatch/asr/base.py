"""Transcriber protocol and the result-shaping shared by all engines.

Engines are selected by config, never by platform sniffing
(docs/conventions.md §5): `mlx` on Apple Silicon for development,
`faster_whisper` everywhere for deployment. Both produce Whisper-style
segment dicts, so the merge logic lives here once and stays testable
without either heavy dependency installed.
"""

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

    total = 0.0
    logprob_acc = 0.0
    nospeech_acc = 0.0
    have_logprob = False
    have_nospeech = False
    for s in segments:
        dur = max(float(s.get("end", 0.0)) - float(s.get("start", 0.0)), 1e-3)
        total += dur
        if "avg_logprob" in s:
            logprob_acc += float(s["avg_logprob"]) * dur
            have_logprob = True
        if "no_speech_prob" in s:
            nospeech_acc += float(s["no_speech_prob"]) * dur
            have_nospeech = True

    avg_logprob = logprob_acc / total if have_logprob else None
    no_speech_prob = nospeech_acc / total if have_nospeech else None
    return text, words, avg_logprob, no_speech_prob
