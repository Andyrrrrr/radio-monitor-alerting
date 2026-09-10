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
from vhfwatch.audio.segmenter import BandGate, Segmenter
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


# -- band-limited gate (D22) -------------------------------------------------
#
# Sub-300 Hz mains hum cannot mask speech — it collapses ~28 dB the instant the
# squelch opens — but it CAN sit above open_threshold_db and hold the gate open
# forever. A mains-powered laptop did exactly that on Parker's rig
# (docs/decisions.md D21). These pin the fix, and that the archive is untouched.

VOICE_HZ = [300.0, 3400.0]


def test_band_gate_sees_speech_band_and_ignores_hum() -> None:
    n = RATE * FRAME_MS // 1000
    gate = BandGate(300.0, 3400.0, RATE, n)
    hum = tone(FRAME_MS, -20.0, freq=120.0)[:n]
    voice = tone(FRAME_MS, -20.0, freq=1000.0)[:n]
    hum_db = 20 * np.log10(max(gate.rms(hum), 1e-10))
    voice_db = 20 * np.log10(max(gate.rms(voice), 1e-10))
    # Equal amplitude in, so equal full-band RMS; the gate must see only one.
    assert voice_db > hum_db + 30


def test_band_gate_agrees_with_plain_rms_for_an_in_band_signal() -> None:
    # The Hann correction exists so the two gate modes stay numerically
    # comparable — without it every threshold would shift on switching mode
    # for reasons unrelated to the band.
    n = RATE * FRAME_MS // 1000
    gate = BandGate(0.0, RATE / 2, RATE, n)
    sig = tone(FRAME_MS, -20.0, freq=1000.0)[:n]
    assert gate.rms(sig) == pytest.approx(float(np.sqrt(np.mean(sig**2))), rel=0.05)


def test_hum_jams_a_full_band_gate_but_not_a_banded_one() -> None:
    # The 2026-09-10 failure reproduced: hum alone, no speech, loud enough to
    # clear the open threshold.
    pcm = np.concatenate([tone(3000, -33.0, freq=120.0), silence(500)])

    wide = SegmenterConfig(open_threshold_db=-40.0, close_threshold_db=-46.0)
    out_wide, _ = run_segmenter(pcm, wide)
    assert len(out_wide) == 1, "full-band gate should emit a segment of pure hum"

    banded = SegmenterConfig(
        open_threshold_db=-40.0, close_threshold_db=-46.0, gate_band_hz=VOICE_HZ
    )
    out_band, _ = run_segmenter(pcm, banded)
    assert out_band == [], "band gate must not open on sub-300 Hz hum"


def test_archived_audio_is_never_band_filtered() -> None:
    # AGENTS.md: original audio is evidence. The gate may look at a band; the
    # Transmission must carry what the radio actually produced.
    speech = tone(1500, -20.0, freq=1000.0) + tone(1500, -26.0, freq=120.0)
    pcm = np.concatenate([silence(400), speech, silence(1200)])
    cfg = SegmenterConfig(
        open_threshold_db=-40.0, close_threshold_db=-46.0, gate_band_hz=VOICE_HZ
    )
    out, _ = run_segmenter(pcm, cfg)
    assert out, "banded gate should still open on real speech"
    spec = np.abs(np.fft.rfft(out[0].pcm))
    freqs = np.fft.rfftfreq(len(out[0].pcm), 1 / RATE)
    hum_bin = float(spec[(freqs > 110) & (freqs < 130)].max())
    voice_bin = float(spec[(freqs > 950) & (freqs < 1050)].max())
    assert hum_bin > voice_bin * 0.05, "hum was filtered out of the archived audio"


def test_full_band_gate_is_unchanged_by_default() -> None:
    # Default config must behave exactly as before this feature existed.
    assert SegmenterConfig().gate_band_hz == []
