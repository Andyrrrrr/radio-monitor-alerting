"""The four required segmenter cases (docs/conventions.md §6) plus the
edges around them. All synthetic, all deterministic — a failure here means
the state machine regressed, not that a threshold needs tuning.

Timing assertions allow one frame (20 ms) of slack: the exact window a
threshold crossing lands in depends on block/window alignment, and pinning
it tighter would make the tests assert implementation detail rather than
behavior.
"""

import numpy as np
import pytest

from tests.synth import BASE, RATE, frames, silence, tone
from vhfwatch.audio.segmenter import Segmenter
from vhfwatch.config import SegmenterConfig
from vhfwatch.models import Transmission

SPEECH_DB = -20.0  # comfortably above the default -42 open threshold
FRAME_MS = 20


def run_segmenter(
    pcm: np.ndarray, cfg: SegmenterConfig | None = None
) -> tuple[list[Transmission], Segmenter]:
    seg = Segmenter(
        cfg or SegmenterConfig(),
        sample_rate=RATE,
        channel="16",
        source_name="test:synth",
    )
    out: list[Transmission] = []
    for frame in frames(pcm):
        out.extend(seg.push(frame))
    tail = seg.flush()
    if tail is not None:
        out.append(tail)
    return out, seg


def assert_close_ms(actual_ms: float, expected_ms: float) -> None:
    assert abs(actual_ms - expected_ms) <= FRAME_MS, (
        f"expected ≈{expected_ms} ms, got {actual_ms} ms"
    )


# -- required case 1: pause mid-transmission must NOT split ----------------


def test_pause_mid_transmission_does_not_split() -> None:
    # "Mayday, mayday, mayday — [breath] — this is vessel Serenity":
    # a 400 ms pause is well inside the 800 ms hang time.
    pcm = np.concatenate(
        [
            silence(500),
            tone(1200, SPEECH_DB),
            silence(400),
            tone(1000, SPEECH_DB),
            silence(2000),
        ]
    )
    txs, _ = run_segmenter(pcm)

    assert len(txs) == 1
    tx = txs[0]
    # Opens at 500 ms, minus 300 ms pre-roll → starts at 200 ms.
    assert_close_ms((tx.started_at - BASE).total_seconds() * 1000, 200)
    # Speech ends at 3100 ms; close fires after the 800 ms hang.
    assert_close_ms((tx.ended_at - BASE).total_seconds() * 1000, 3900)
    # Level stats read the voiced span, not the pre-roll/hang quiet: the
    # 2200 ms of tone plus the 400 ms pause dilute -20 dBFS by ~0.7 dB.
    assert tx.rms_dbfs == pytest.approx(-20.7, abs=1.0)
    assert tx.est_snr_db is not None and tx.est_snr_db > 100  # vs digital silence


# -- required case 2: sub-threshold blip must NOT open ---------------------


def test_short_blip_does_not_open() -> None:
    # 20 ms above threshold = 1 window < open_frames(2). A squelch crash.
    pcm = np.concatenate([silence(1000), tone(20, SPEECH_DB), silence(1000)])
    txs, seg = run_segmenter(pcm)
    assert txs == []
    assert seg.discarded == 0  # never opened, so nothing was even discarded


def test_quiet_hum_does_not_open() -> None:
    # Sustained but below the open threshold (-50 < -42): line noise.
    pcm = np.concatenate([silence(500), tone(1500, -50.0), silence(500)])
    txs, _ = run_segmenter(pcm)
    assert txs == []


def test_loud_but_short_burst_is_discarded() -> None:
    # Long enough to open (200 ms > 40 ms), too short to be speech
    # (200 ms < min_duration 600 ms). Must be dropped — and counted.
    pcm = np.concatenate([silence(1000), tone(200, SPEECH_DB), silence(1500)])
    txs, seg = run_segmenter(pcm)
    assert txs == []
    assert seg.discarded == 1


# -- required case 3: stuck carrier must force-close ------------------------


def test_stuck_carrier_force_closes() -> None:
    cfg = SegmenterConfig(max_duration_ms=2000)  # small for test speed
    pcm = np.concatenate([silence(500), tone(5000, SPEECH_DB), silence(1200)])
    txs, _ = run_segmenter(pcm, cfg)

    # 5 s of carrier against a 2 s cap: two force-closed chunks plus the
    # ~1 s remainder closed by hang time. Nothing lost, nothing unbounded.
    assert len(txs) == 3
    assert_close_ms(txs[0].duration_ms, 2300)  # 2000 voiced + 300 pre-roll
    total_voiced = sum(tx.duration_ms for tx in txs)
    assert total_voiced > 5000  # the whole carrier is accounted for


# -- required case 4: transmission starting at t=0 --------------------------


def test_transmission_at_stream_start() -> None:
    # Pre-roll edge case: there is no audio before t=0 to prepend. Must
    # emit from the very first sample without crashing or waiting.
    pcm = np.concatenate([tone(1000, SPEECH_DB), silence(1500)])
    txs, _ = run_segmenter(pcm)

    assert len(txs) == 1
    tx = txs[0]
    assert tx.started_at == BASE  # no pre-roll available — starts at t=0
    assert_close_ms((tx.ended_at - BASE).total_seconds() * 1000, 1800)  # 1000 + hang
    # No quiet was seen before opening, so there is no noise estimate yet —
    # est_snr_db must be honestly None, not a made-up number.
    assert tx.est_snr_db is None


# -- edges ------------------------------------------------------------------


def test_stream_ending_mid_transmission_is_flushed() -> None:
    # File ends while someone is still talking: flush() must emit it.
    pcm = np.concatenate([silence(500), tone(1000, SPEECH_DB)])
    txs, _ = run_segmenter(pcm)
    assert len(txs) == 1
    assert_close_ms((txs[0].ended_at - txs[0].started_at).total_seconds() * 1000, 1300)


def test_two_transmissions_stay_separate() -> None:
    # Gap (2 s) well beyond hang time: must be two records, in order.
    pcm = np.concatenate(
        [
            silence(500),
            tone(800, SPEECH_DB),
            silence(2000),
            tone(800, SPEECH_DB),
            silence(1500),
        ]
    )
    txs, _ = run_segmenter(pcm)
    assert len(txs) == 2
    assert txs[0].ended_at <= txs[1].started_at


def test_wrong_sample_rate_is_rejected() -> None:
    seg = Segmenter(SegmenterConfig(), sample_rate=RATE, channel="16", source_name="t")
    bad = frames(silence(100))[0]
    bad.sample_rate = 48000
    with pytest.raises(ValueError, match="48000"):
        seg.push(bad)
