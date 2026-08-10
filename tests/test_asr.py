"""ASR result-shaping and engine selection.

The engines themselves need multi-hundred-MB models and real inference, so
they are exercised on hardware via scripts/replay.py — what's tested here
is everything around them: the segment merge that feeds the hallucination
gate, and the config-driven selector's failure modes.
"""

import pytest

from vhfwatch.asr import create_transcriber
from vhfwatch.asr.base import SegmentDict, merge_segments
from vhfwatch.config import AsrConfig


def test_merge_empty_is_all_none() -> None:
    text, words, avg_logprob, no_speech_prob = merge_segments([])
    assert text == ""
    assert words == []
    assert avg_logprob is None
    assert no_speech_prob is None


def test_merge_single_segment() -> None:
    segments: list[SegmentDict] = [
        {
            "text": " Mayday mayday ",
            "start": 0.0,
            "end": 2.0,
            "avg_logprob": -0.3,
            "no_speech_prob": 0.1,
            "words": [
                {"word": " Mayday", "start": 0.0, "end": 0.8, "probability": 0.9},
                {"word": " mayday", "start": 1.0, "end": 1.8, "probability": 0.7},
            ],
        }
    ]
    text, words, avg_logprob, no_speech_prob = merge_segments(segments)
    assert text == "Mayday mayday"
    assert [w.text for w in words] == ["Mayday", "mayday"]
    assert words[0].confidence == 0.9
    assert avg_logprob == pytest.approx(-0.3)
    assert no_speech_prob == pytest.approx(0.1)


def test_merge_stats_are_duration_weighted() -> None:
    # A long clean segment plus a short noise tail: the tail must not be
    # able to drag the merged stats to either extreme.
    segments: list[SegmentDict] = [
        {
            "text": "long clean speech",
            "start": 0.0,
            "end": 9.0,
            "avg_logprob": -0.2,
            "no_speech_prob": 0.05,
        },
        {
            "text": "noise",
            "start": 9.0,
            "end": 10.0,
            "avg_logprob": -1.4,
            "no_speech_prob": 0.9,
        },
    ]
    text, _, avg_logprob, no_speech_prob = merge_segments(segments)
    assert text == "long clean speech noise"
    assert avg_logprob == pytest.approx((-0.2 * 9 - 1.4 * 1) / 10)
    assert no_speech_prob == pytest.approx((0.05 * 9 + 0.9 * 1) / 10)


def test_selector_rejects_deepgram_loudly() -> None:
    cfg = AsrConfig(engine="deepgram")
    with pytest.raises(RuntimeError, match="not implemented in the POC"):
        create_transcriber(cfg)
