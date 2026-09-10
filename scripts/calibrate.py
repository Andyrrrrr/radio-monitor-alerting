#!/usr/bin/env python
"""Measure the noise floor and suggest segmenter thresholds.

Run during hardware bring-up (docs/hardware.md §5) with the radio ON,
squelch CLOSED, and nobody transmitting — what's measured is the quiet the
segmenter must distinguish speech from. Values are per-rig and never
transfer between machines (docs/decisions.md D11).

    python scripts/calibrate.py --seconds 30

Paste the printed TOML into your config/config.toml, then add a dated
entry to docs/bringup-log.md — knob positions included.
"""

import argparse
from pathlib import Path

import numpy as np
import sounddevice as sd

from vhfwatch.audio.segmenter import BandGate
from vhfwatch.audio.sources import resolve_input_device
from vhfwatch.config import load_config

# Thresholds are derived from the noise TAIL, not the median.
#
# The gate closes only after hang_ms of CONTINUOUS quiet, so a single noisy
# window resets the timer. What matters is therefore the loudest window the
# idle line produces, not its average. Measured 2026-09-10 on Parker's rig:
# at the segmenter's 20 ms window the idle noise has median -40.0 dBFS but a
# maximum of -34.9 — a 5 dB tail. A close threshold set from the median sat
# inside that tail, was crossed ~1.5 times a second, and the gate never
# closed: every segment ran to max_duration_ms (docs/decisions.md D22).
CLOSE_ABOVE_TAIL_DB = 2.0
HYSTERESIS_DB = 4.0


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(x**2)))
    return 20.0 * np.log10(max(rms, 1e-10))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    audio = cfg.audio
    device = resolve_input_device(audio.device)

    print(
        f"Recording {args.seconds}s from "
        f"{audio.device or '(default device)'} at {audio.capture_rate} Hz.\n"
        "Radio ON, squelch CLOSED, nobody transmitting...\n"
    )
    recording = sd.rec(
        int(args.seconds * audio.capture_rate),
        samplerate=audio.capture_rate,
        channels=audio.input_channels,
        dtype="float32",
        device=device,
    )
    sd.wait()

    # Same signal path the segmenter sees: slice one channel, apply gain.
    pcm = recording[:, audio.use_channel]
    if audio.input_gain_db:
        pcm = np.clip(pcm * 10 ** (audio.input_gain_db / 20), -1.0, 1.0)

    # Measure at the SEGMENTER'S window length, not a convenient 100 ms.
    # Short windows average less noise and so have a markedly higher tail:
    # the same idle line reads p90 -40.0 at 100 ms and -37.0 at 20 ms. Reading
    # at 100 ms and gating at 20 ms is why thresholds derived here used to be
    # too low to close (docs/decisions.md D22).
    window = audio.capture_rate * cfg.segmenter.frame_ms // 1000

    # Measure the floor the SAME way the segmenter's gate will. If the gate
    # is band-limited and this were not, the printed threshold would be
    # derived from a full-band floor and land ~10 dB too high — the gate
    # would then miss most transmissions, silently. Same reading, same
    # meaning (docs/decisions.md D22).
    band = cfg.segmenter.gate_band_hz
    if band:
        gate = BandGate(band[0], band[1], audio.capture_rate, window)
        levels = np.array(
            [
                20.0 * np.log10(max(gate.rms(pcm[i : i + window]), 1e-10))
                for i in range(0, len(pcm) - window, window)
            ]
        )
        print(f"gate band: {band[0]:.0f}-{band[1]:.0f} Hz (segmenter.gate_band_hz)\n")
    else:
        levels = np.array(
            [dbfs(pcm[i : i + window]) for i in range(0, len(pcm) - window, window)]
        )
    floor = float(np.median(levels))
    p90 = float(np.percentile(levels, 90))
    tail = float(np.max(levels))
    peak = 20.0 * np.log10(max(float(np.max(np.abs(pcm))), 1e-10))

    print(f"noise floor (median {cfg.segmenter.frame_ms}ms RMS): {floor:6.1f} dBFS")
    print(f"90th percentile:                {p90:6.1f} dBFS")
    print(f"loudest idle window (the tail): {tail:6.1f} dBFS")
    print(f"peak sample:                    {peak:6.1f} dBFS")

    if peak > -0.5:
        print(
            "\n!! CLIPPING during supposed silence — the input level is far"
            "\n!! too hot. Fix the analog chain before trusting any numbers"
            "\n!! here (docs/hardware.md §3.5)."
        )
    if p90 - floor > 6:
        print(
            "\n!! Noise floor is unstable (p90 is >6 dB over the median)."
            "\n!! Something intermittent is on the line — find it first."
        )
    if floor > -30:
        print(
            "\n!! Floor above -30 dBFS is suspiciously loud for closed"
            "\n!! squelch — check gain staging before calibrating."
        )
    if floor > cfg.segmenter.open_threshold_db:
        print(
            "\n!! The measured floor is ABOVE the configured open_threshold_db"
            f"\n!! ({cfg.segmenter.open_threshold_db}). The gate cannot close: every"
            "\n!! segment will run to max_duration_ms and Whisper will be fed"
            "\n!! noise. Paste the thresholds below, or find what raised the"
            "\n!! floor first (docs/decisions.md D21 — a mains-powered laptop"
            "\n!! did exactly this, and this script gave no other warning)."
        )

    close_db = round(tail + CLOSE_ABOVE_TAIL_DB, 1)
    open_db = round(close_db + HYSTERESIS_DB, 1)
    print(
        "\nPaste into config/config.toml (then log it in "
        "docs/bringup-log.md):\n"
        f"""
[audio.calibration]
noise_floor_dbfs = {floor:.1f}

[segmenter]
open_threshold_db  = {open_db}
close_threshold_db = {close_db}
"""
    )


if __name__ == "__main__":
    main()
