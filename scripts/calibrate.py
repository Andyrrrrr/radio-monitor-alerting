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

from vhfwatch.audio.sources import resolve_input_device
from vhfwatch.config import load_config

# Suggested threshold offsets above the measured floor
# (docs/architecture.md §5.2: open ≈ floor + 10 dB; hysteresis 6 dB).
OPEN_ABOVE_FLOOR_DB = 10.0
HYSTERESIS_DB = 6.0


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

    window = audio.capture_rate // 10  # 100 ms windows
    levels = np.array(
        [dbfs(pcm[i : i + window]) for i in range(0, len(pcm) - window, window)]
    )
    floor = float(np.median(levels))
    p90 = float(np.percentile(levels, 90))
    peak = 20.0 * np.log10(max(float(np.max(np.abs(pcm))), 1e-10))

    print(f"noise floor (median 100ms RMS): {floor:6.1f} dBFS")
    print(f"90th percentile:                {p90:6.1f} dBFS")
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

    open_db = round(floor + OPEN_ABOVE_FLOOR_DB, 1)
    close_db = round(open_db - HYSTERESIS_DB, 1)
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
