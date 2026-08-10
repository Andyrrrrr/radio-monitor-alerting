"""Resampling: native capture rate → the pipeline's 16 kHz.

Whisper wants 16 kHz and marine VHF audio is ~3 kHz bandwidth, so nothing
is lost (docs/architecture.md §5.1). soxr does the anti-aliased conversion
— proper resampling is exactly the DSP we should not hand-roll
(docs/decisions.md D13's revisit clause, cashed in here).

Implemented as a wrapping AudioSource so file and live sources stay
interchangeable: the pipeline sees 16 kHz frames either way.
"""

from collections.abc import AsyncIterator

import numpy as np
import numpy.typing as npt
import soxr
import structlog

from vhfwatch.audio.sources import AudioSource
from vhfwatch.models import AudioFrame

logger = structlog.get_logger(__name__)


class ResamplingAudioSource:
    """Wraps any AudioSource and yields frames at target_rate.

    Streaming (chunk-aware) resampling, not per-frame independent calls —
    a per-chunk resampler would glitch at every block boundary.
    """

    def __init__(self, inner: AudioSource, target_rate: int) -> None:
        self._inner = inner
        self.name = inner.name
        self.sample_rate = target_rate
        self._stream: soxr.ResampleStream | None = None
        if inner.sample_rate != target_rate:
            self._stream = soxr.ResampleStream(
                inner.sample_rate, target_rate, num_channels=1, dtype="float32"
            )
            logger.info(
                "audio.resampling",
                source=inner.name,
                from_rate=inner.sample_rate,
                to_rate=target_rate,
            )

    async def frames(self) -> AsyncIterator[AudioFrame]:
        if self._stream is None:
            # Already at target rate — pass through untouched.
            async for frame in self._inner.frames():
                yield frame
            return

        async for frame in self._inner.frames():
            pcm: npt.NDArray[np.float32] = self._stream.resample_chunk(frame.pcm)
            if len(pcm) == 0:
                continue  # resampler still buffering its filter tail
            # captured_at carries over: the filter delay is a few ms, far
            # below anything the segmenter timestamps care about.
            yield AudioFrame(
                pcm=pcm,
                sample_rate=self.sample_rate,
                captured_at=frame.captured_at,
                source_name=frame.source_name,
            )

    async def close(self) -> None:
        await self._inner.close()
