"""Audio archival.

Phase 0 writes plain 16-bit WAV. The real archive format is Opus with an
AAC playback copy generated at ingest (docs/architecture.md §5.8) — that
lands in Phase 3 behind this same call site, so the pipeline won't change.

The SHA-256 is computed over the file as written, so a record's hash can be
verified against the archived bytes at any later date (docs/decisions.md D9).
"""

import hashlib
import wave
from pathlib import Path

import numpy as np

from vhfwatch.models import Transmission


def archive_wav(tx: Transmission, audio_dir: Path) -> tuple[Path, str]:
    """Write a transmission's PCM to disk; returns (path, sha256 hex)."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / f"{tx.id}.wav"
    ints = (np.clip(tx.pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(tx.sample_rate)
        wf.writeframes(ints.tobytes())
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, sha256
