"""Hallucination gates: the filter that keeps Whisper's inventions away from
the watchword matcher (docs/architecture.md §5.4).

The dangerous failure here is asymmetric: a gate that's too loose fires
false alerts, but a gate that's too tight silently discards a real mayday.
The tests pin both directions.
"""

from datetime import UTC, datetime, timedelta

import numpy as np

from vhfwatch.config import AsrConfig, HallucinationConfig
from vhfwatch.detect.hallucination import gate_transcript, gate_transmission
from vhfwatch.models import Transcript, Transmission, new_id

HALL = HallucinationConfig()
ASR = AsrConfig()


def make_transmission(est_snr_db: float | None) -> Transmission:
    started = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
    return Transmission(
        id=new_id(),
        channel="16",
        started_at=started,
        ended_at=started + timedelta(seconds=4),
        duration_ms=4000,
        pcm=np.zeros(64000, dtype=np.float32),
        sample_rate=16000,
        rms_dbfs=-20.0,
        peak_dbfs=-6.0,
        est_snr_db=est_snr_db,
        audio_path=None,
        audio_sha256=None,
        source="test",
    )


def make_transcript(
    text: str,
    avg_logprob: float | None = -0.3,
    no_speech_prob: float | None = 0.05,
) -> Transcript:
    return Transcript(
        transmission_id=new_id(),
        engine="test:none",
        text=text,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
    )


# -- pre-ASR ----------------------------------------------------------------


def test_low_snr_rejected_with_reason() -> None:
    reason = gate_transmission(make_transmission(est_snr_db=1.0), HALL)
    assert reason is not None
    assert "snr" in reason


def test_good_snr_passes() -> None:
    assert gate_transmission(make_transmission(est_snr_db=12.0), HALL) is None


def test_unknown_snr_passes() -> None:
    # No noise floor measured yet (first transmission after startup) must
    # NOT block transcription — bias toward transcribing unknowns.
    assert gate_transmission(make_transmission(est_snr_db=None), HALL) is None


# -- post-ASR ---------------------------------------------------------------


def test_clean_distress_transcript_passes() -> None:
    t = make_transcript("mayday mayday mayday this is vessel Serenity")
    assert gate_transcript(t, HALL, ASR) is None


def test_empty_rejected() -> None:
    assert gate_transcript(make_transcript("   "), HALL, ASR) == "empty transcript"


def test_high_no_speech_prob_rejected() -> None:
    t = make_transcript("thank you", no_speech_prob=0.95)
    reason = gate_transcript(t, HALL, ASR)
    assert reason is not None
    assert "no_speech_prob" in reason


def test_poor_logprob_rejected() -> None:
    t = make_transcript("garbled noise text", avg_logprob=-1.8)
    reason = gate_transcript(t, HALL, ASR)
    assert reason is not None
    assert "avg_logprob" in reason


def test_missing_quality_stats_pass_through() -> None:
    # An engine that doesn't report stats must not have everything rejected.
    t = make_transcript(
        "pan pan this is vessel serenity", avg_logprob=None, no_speech_prob=None
    )
    assert gate_transcript(t, HALL, ASR) is None


def test_blocklisted_artifact_rejected_case_insensitive() -> None:
    t = make_transcript("Thank You For Watching!")
    reason = gate_transcript(t, HALL, ASR)
    assert reason is not None
    assert "blocklisted" in reason


def test_triple_mayday_is_not_a_repetition_loop() -> None:
    # Real distress procedure repeats the word exactly three times.
    t = make_transcript("mayday mayday mayday")
    assert gate_transcript(t, HALL, ASR) is None


def test_repetition_loop_rejected() -> None:
    t = make_transcript("the " * 10)
    reason = gate_transcript(t, HALL, ASR)
    assert reason is not None
    assert "repeated" in reason
