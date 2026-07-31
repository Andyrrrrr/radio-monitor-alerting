"""AudioSource implementations.

The Protocol is the seam that makes offline development possible: the
pipeline runs identically over a recorded WAV and a live radio
(docs/architecture.md §5.1). LiveAudioSource lands in Phase 2;
DirectoryAudioSource (corpus replay in timestamp order) with it.
"""

import asyncio
import wave
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from vhfwatch.models import AudioFrame

# How often the non-realtime file reader yields to the event loop. Purely
# cooperative scheduling, not a tunable — nothing about a rig or site
# changes it.
_YIELD_EVERY_BLOCKS = 64


class AudioSource(Protocol):
    name: str
    sample_rate: int

    def frames(self) -> AsyncIterator[AudioFrame]: ...

    async def close(self) -> None: ...


class FileAudioSource:
    """Reads a 16-bit PCM WAV and yields it as AudioFrames.

    `realtime=False` (the default) runs as fast as possible — that's what
    makes tests deterministic and corpus runs quick. `realtime=True` paces
    to roughly wall clock for watching a replay behave like live input.

    Only 16-bit PCM is supported for now: it's what the corpus recorder and
    the synthetic fixtures produce, and it keeps us on the stdlib `wave`
    module. If other formats show up, that's the moment to add `soundfile`
    (Phase 2, alongside live capture) — not to hand-roll 24-bit unpacking.
    """

    def __init__(
        self,
        path: Path | str,
        blocksize: int = 1024,
        channel: int = 0,
        realtime: bool = False,
        start_at: datetime | None = None,
    ) -> None:
        self._path = Path(path)
        self._blocksize = blocksize
        self._channel = channel
        self._realtime = realtime
        self._start_at = start_at
        # Not a context manager on purpose: the reader must stay open across
        # the whole async iteration; close() is the lifecycle.
        self._wav = wave.open(str(self._path), "rb")  # noqa: SIM115
        if self._wav.getsampwidth() != 2:
            raise ValueError(
                f"{self._path}: only 16-bit PCM WAV is supported "
                f"(got sample width {self._wav.getsampwidth()} bytes); "
                "re-export the file or wait for soundfile support in Phase 2"
            )
        self._channels = self._wav.getnchannels()
        if channel >= self._channels:
            raise ValueError(
                f"{self._path}: channel {channel} requested but file has "
                f"{self._channels} channel(s)"
            )
        self.name = f"file:{self._path.name}"
        self.sample_rate = self._wav.getframerate()

    async def frames(self) -> AsyncIterator[AudioFrame]:
        # captured_at is the time of the frame's FIRST sample; downstream
        # timestamps are derived from it plus sample offsets.
        t = self._start_at if self._start_at is not None else datetime.now(UTC)
        blocks = 0
        while True:
            raw = self._wav.readframes(self._blocksize)
            if not raw:
                return
            interleaved = np.frombuffer(raw, dtype=np.int16)
            deinterleaved = interleaved.reshape(-1, self._channels)
            # Slice one channel, never average — mixing a live channel with
            # a silent one costs 6 dB and invalidates VAD calibration
            # (docs/hardware.md §3.7).
            pcm: npt.NDArray[np.float32] = (
                deinterleaved[:, self._channel].astype(np.float32) / 32768.0
            )
            yield AudioFrame(
                pcm=pcm,
                sample_rate=self.sample_rate,
                captured_at=t,
                source_name=self.name,
            )
            block_s = len(pcm) / self.sample_rate
            t += timedelta(seconds=block_s)
            blocks += 1
            if self._realtime:
                await asyncio.sleep(block_s)
            elif blocks % _YIELD_EVERY_BLOCKS == 0:
                await asyncio.sleep(0)  # stay cooperative on long files

    async def close(self) -> None:
        self._wav.close()
