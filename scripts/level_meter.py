#!/usr/bin/env python
"""Live input level meter for setting the analog gain by hand.

Setting a trim needs immediate feedback, which `calibrate.py` can't give —
it records for N seconds and reports afterwards. Run this instead while you
turn the knob, then run `calibrate.py` once the level is right to derive
segmenter thresholds (docs/hardware.md §5 Step 4).

    python scripts/level_meter.py                  # watch levels, write nothing
    python scripts/level_meter.py --record out.wav # explicit start/stop capture

Reads the same device, channel slice, and digital gain the pipeline uses, so
what it shows is what the segmenter would see. Ctrl-C to stop; a summary and
a verdict print on exit.

Targets (docs/hardware.md §3.5): speech peaks -12 to -6 dBFS, never
touching 0. Clipping is destructive and cannot be undone downstream —
always fix it in the analog domain, not with input_gain_db.
"""

import argparse
import queue
import sys
import time
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from vhfwatch.audio.sources import resolve_input_device
from vhfwatch.config import AudioConfig, load_config

# Marine VHF voice occupies roughly 300-3400 Hz. Splitting the level into
# bands separates real audio from mains hum on an idle line, which otherwise
# reads as a misleadingly high noise floor (see docs/bringup-log.md).
VOICE_BAND_HZ = (300.0, 3400.0)
MAINS_BAND_HZ = (50.0, 70.0)

UPDATE_HZ = 5.0  # display refreshes per second
BAR_WIDTH = 40
BAR_FLOOR_DB = -72.0  # left end of the bar

TARGET_PEAK_MIN_DB = -12.0
TARGET_PEAK_MAX_DB = -6.0
CLIP_DB = -0.5  # at or above this, treat as clipped


def dbfs(pcm: npt.NDArray[np.float32]) -> float:
    """RMS level of a block, in dBFS."""
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    return float(20.0 * np.log10(max(rms, 1e-12)))


def peak_dbfs(pcm: npt.NDArray[np.float32]) -> float:
    return float(20.0 * np.log10(max(float(np.max(np.abs(pcm))), 1e-12)))


def band_dbfs(pcm: npt.NDArray[np.float32], rate: int, lo: float, hi: float) -> float:
    """Level within a frequency band, in dB relative to full scale."""
    windowed = pcm.astype(np.float64) * np.hanning(len(pcm))
    spectrum = np.abs(np.fft.rfft(windowed)) / (len(pcm) / 4)
    freqs = np.fft.rfftfreq(len(pcm), 1.0 / rate)
    mask = (freqs >= lo) & (freqs < hi)
    power = float(np.sqrt(np.sum(spectrum[mask] ** 2)))
    return float(20.0 * np.log10(max(power, 1e-12)))


def bar(level_db: float) -> str:
    """A crude horizontal meter — enough to set a knob by."""
    fraction = (level_db - BAR_FLOOR_DB) / (0.0 - BAR_FLOOR_DB)
    filled = round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    return "#" * filled + "-" * (BAR_WIDTH - filled)


class WavWriter:
    """16-bit mono WAV of the sliced channel, opened on explicit start."""

    def __init__(self, path: Path, rate: int):
        self._path = path
        # Not a context manager: it stays open across callbacks and is
        # closed on shutdown (same pattern as record_corpus.py).
        self._wav = wave.open(str(path), "wb")  # noqa: SIM115
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)
        self._frames = 0
        self._rate = rate

    def write(self, pcm: npt.NDArray[np.float32]) -> None:
        ints = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
        self._wav.writeframes(ints.tobytes())
        self._frames += len(pcm)

    @property
    def seconds(self) -> float:
        return self._frames / self._rate

    def close(self) -> None:
        self._wav.close()


def verdict(max_peak_db: float, clipped: bool) -> str:
    """Plain-language read on whether the analog gain is set correctly."""
    if clipped:
        return (
            "CLIPPED — the analog level is too hot. Turn the interface trim "
            "DOWN (or the radio volume down if there is no trim) and re-run. "
            "Clipping destroys audio upstream of anything software can fix; "
            "input_gain_db will NOT repair it (docs/hardware.md §3.5)."
        )
    if max_peak_db > TARGET_PEAK_MAX_DB:
        return (
            f"HOT — peaks reached {max_peak_db:.1f} dBFS, above the "
            f"{TARGET_PEAK_MAX_DB:.0f} dBFS target. Not clipping yet, but "
            "there is no headroom for a louder station. Trim down a little."
        )
    if max_peak_db < -25.0:
        return (
            f"QUIET — peaks only reached {max_peak_db:.1f} dBFS. If that was "
            "a strong local signal, trim up. If it was a weak distant "
            "station, this may be correct — judge it on a strong one."
        )
    if max_peak_db < TARGET_PEAK_MIN_DB:
        return (
            f"SLIGHTLY QUIET — peaks reached {max_peak_db:.1f} dBFS, below "
            f"the {TARGET_PEAK_MIN_DB:.0f} dBFS target. Usable; trim up a "
            "little if this was your strongest signal."
        )
    return (
        f"IN RANGE — peaks reached {max_peak_db:.1f} dBFS, inside the "
        f"{TARGET_PEAK_MIN_DB:.0f} to {TARGET_PEAK_MAX_DB:.0f} dBFS target. "
        "Tape the knobs, note them in docs/bringup-log.md, then run "
        "scripts/calibrate.py with no one transmitting."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        type=Path,
        default=None,
        help="also save the session to this WAV (waits for Enter to start)",
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    audio: AudioConfig = cfg.audio
    device = resolve_input_device(audio.device)
    gain = 10 ** (audio.input_gain_db / 20) if audio.input_gain_db else 1.0

    blocks: queue.Queue[npt.NDArray[np.float32]] = queue.Queue()

    def callback(
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"\n!! capture status: {status}")
        # Slice one channel, never average (docs/hardware.md §3.7).
        blocks.put(indata[:, audio.use_channel].copy())

    print(
        f"device={audio.device or '(default)'} index={device} "
        f"rate={audio.capture_rate} channel_slice={audio.use_channel} "
        f"input_gain_db={audio.input_gain_db}"
    )
    print(
        f"target: speech peaks {TARGET_PEAK_MIN_DB:.0f} to "
        f"{TARGET_PEAK_MAX_DB:.0f} dBFS, never touching 0\n"
    )

    writer: WavWriter | None = None
    if args.record is not None:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        input("press Enter to START recording (Ctrl-C stops)... ")

    max_peak = -999.0
    clipped = False
    idle_levels: list[float] = []
    started = time.monotonic()

    try:
        with sd.InputStream(
            device=device,
            channels=audio.input_channels,
            samplerate=audio.capture_rate,
            blocksize=audio.blocksize,
            dtype="float32",
            callback=callback,
        ):
            if args.record is not None:
                writer = WavWriter(args.record, audio.capture_rate)
                print(f"\n>> RECORDING → {args.record}\n")
            else:
                print(">> WATCHING — nothing is being written to disk\n")

            window: list[npt.NDArray[np.float32]] = []
            needed = int(audio.capture_rate / UPDATE_HZ)
            while True:
                window.append(blocks.get())
                if sum(len(b) for b in window) < needed:
                    continue
                pcm = np.concatenate(window)
                window = []
                if gain != 1.0:
                    pcm = np.clip(pcm * gain, -1.0, 1.0)
                if writer is not None:
                    writer.write(pcm)

                rms = dbfs(pcm)
                peak = peak_dbfs(pcm)
                voice = band_dbfs(pcm, audio.capture_rate, *VOICE_BAND_HZ)
                mains = band_dbfs(pcm, audio.capture_rate, *MAINS_BAND_HZ)
                max_peak = max(max_peak, peak)
                if peak >= CLIP_DB:
                    clipped = True
                # Quiet windows only, so the floor estimate isn't polluted
                # by speech (mirrors the segmenter's closed-frame EMA).
                if peak < -30.0:
                    idle_levels.append(rms)

                flag = " CLIP!" if peak >= CLIP_DB else ""
                sys.stdout.write(
                    f"\r[{bar(rms)}] rms {rms:6.1f}  peak {peak:6.1f}  "
                    f"max {max_peak:6.1f}  voice {voice:6.1f}  "
                    f"hum {mains:6.1f}{flag}   "
                )
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.monotonic() - started
        if writer is not None:
            writer.close()
            print(f"\n\n<< STOPPED — wrote {writer.seconds:.1f}s to {args.record}")
        else:
            print(f"\n\n<< STOPPED after {elapsed:.1f}s (nothing written)")

        print("\nsummary")
        print(f"  loudest peak      : {max_peak:6.1f} dBFS")
        if idle_levels:
            print(f"  idle floor (median): {np.median(idle_levels):6.1f} dBFS")
        else:
            print("  idle floor        : not measured (never quiet enough)")
        print(f"\n{verdict(max_peak, clipped)}")


if __name__ == "__main__":
    main()
