"""Synthetic audio for tests and fixtures.

Everything committed to the repo must be generated here — no real radio
traffic, ever (AGENTS.md constraint 8). Deterministic on purpose: sines,
not noise, so boundary assertions are exact.
"""

import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import numpy.typing as npt

from vhfwatch.models import AudioFrame

RATE = 16000
BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def tone(ms: int, level_dbfs: float, freq: float = 400.0) -> npt.NDArray[np.float32]:
    """A sine at the given RMS level in dBFS (stands in for speech)."""
    n = RATE * ms // 1000
    t = np.arange(n, dtype=np.float32) / RATE
    amplitude = 10 ** (level_dbfs / 20) * np.sqrt(2)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(ms: int) -> npt.NDArray[np.float32]:
    return np.zeros(RATE * ms // 1000, dtype=np.float32)


def frames(
    pcm: npt.NDArray[np.float32],
    blocksize: int = 1024,
    start: datetime = BASE,
) -> list[AudioFrame]:
    """Chop a signal into AudioFrames the way a source would deliver it."""
    out = []
    for i in range(0, len(pcm), blocksize):
        block = pcm[i : i + blocksize]
        out.append(
            AudioFrame(
                pcm=block,
                sample_rate=RATE,
                captured_at=start + timedelta(seconds=i / RATE),
                source_name="test:synth",
            )
        )
    return out


def write_wav16(path: Path, pcm: npt.NDArray[np.float32], channels: int = 1) -> None:
    """Write float32 PCM as 16-bit WAV (what FileAudioSource reads)."""
    ints = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(ints.tobytes())
