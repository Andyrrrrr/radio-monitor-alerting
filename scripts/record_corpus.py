#!/usr/bin/env python
"""Continuous corpus recording with rotating timestamped files.

Start this as early as possible and leave it running: real Ch 16 audio
accumulates in wall-clock time and cannot be rushed later — it's what every
tuning decision depends on (docs/roadmap.md). The corpus is shareable
between rigs; calibration values are not (docs/decisions.md D11).

    python scripts/record_corpus.py --out data/corpus --channel 16

Files are named by their UTC start time (20260810-193000Z-ch16.wav), so
filename order is timestamp order — DirectoryAudioSource depends on that.
Recording is at the native capture rate for maximum fidelity; the pipeline
resamples on replay. NEVER commit the output — it's real people's voices
(AGENTS.md constraint 8).
"""

import argparse
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from vhfwatch.audio.sources import resolve_input_device
from vhfwatch.config import AudioConfig, load_config


class RotatingRecorder:
    """Writes the sliced capture channel to 16-bit WAVs, rotating on size.

    The corpus stores the raw capture — no digital gain — so it stays
    valid even if input_gain_db changes later.
    """

    def __init__(
        self, out_dir: Path, channel_label: str, audio: AudioConfig, rotate_frames: int
    ):
        self._out_dir = out_dir
        self._label = channel_label
        self._audio = audio
        self._rotate_frames = rotate_frames
        self._wav: wave.Wave_write | None = None
        self._written = 0

    def _open(self) -> wave.Wave_write:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
        path = self._out_dir / f"{stamp}-ch{self._label}.wav"
        # Not a context manager: the file stays open across callbacks and
        # is closed by rotation or shutdown.
        wf = wave.open(str(path), "wb")  # noqa: SIM115
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(self._audio.capture_rate)
        print(f"recording → {path}")
        return wf

    def write(self, indata: npt.NDArray[np.float32]) -> None:
        if self._wav is None:
            self._wav = self._open()
            self._written = 0
        # Slice one channel, never average (docs/hardware.md §3.7).
        mono = indata[:, self._audio.use_channel]
        ints = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16)
        self._wav.writeframes(ints.tobytes())
        self._written += len(mono)
        if self._written >= self._rotate_frames:
            self._wav.close()
            self._wav = None

    def close(self) -> None:
        if self._wav is not None:
            self._wav.close()
            self._wav = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/corpus"))
    parser.add_argument("--channel", default="16", help="channel label for filenames")
    parser.add_argument("--minutes-per-file", type=int, default=15)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    audio = cfg.audio
    device = resolve_input_device(audio.device)
    args.out.mkdir(parents=True, exist_ok=True)

    recorder = RotatingRecorder(
        args.out, args.channel, audio, args.minutes_per_file * 60 * audio.capture_rate
    )

    def callback(
        indata: npt.NDArray[np.float32],
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"!! capture status: {status}")
        recorder.write(indata)

    print(
        f"device={audio.device or '(default)'} rate={audio.capture_rate} "
        f"channel_slice={audio.use_channel} rotate={args.minutes_per_file}min "
        "— Ctrl-C to stop"
    )
    try:
        with sd.InputStream(
            device=device,
            channels=audio.input_channels,
            samplerate=audio.capture_rate,
            blocksize=audio.blocksize,
            dtype="float32",
            callback=callback,
        ):
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        recorder.close()
        print("stopped.")


if __name__ == "__main__":
    main()
