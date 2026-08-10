"""faster-whisper transcriber — the portable deployment default.

CTranslate2 backend: CPU-only on macOS (no Metal) but runs everywhere,
which is what matters — this is the engine the x86 Linux box will use
(docs/conventions.md §5). Keep it working even while developing on MLX.
"""

import asyncio
import time
from typing import Any

import numpy as np
import structlog

from vhfwatch.asr.base import SegmentDict, merge_segments
from vhfwatch.config import AsrConfig
from vhfwatch.models import Transcript, Transmission

logger = structlog.get_logger(__name__)

_WARMUP_SAMPLES = 16000


class FasterWhisperTranscriber:
    def __init__(self, cfg: AsrConfig) -> None:
        from faster_whisper import WhisperModel

        self._cfg = cfg
        self._model = WhisperModel(
            cfg.model, device=cfg.fw_device, compute_type=cfg.fw_compute_type
        )
        self.name = f"faster-whisper:{cfg.model}"

    def _transcribe_sync(self, pcm: np.ndarray) -> tuple[list[SegmentDict], Any]:
        segments_iter, info = self._model.transcribe(
            pcm,
            beam_size=self._cfg.beam_size,
            temperature=self._cfg.temperature,
            condition_on_previous_text=self._cfg.condition_on_previous_text,
            no_speech_threshold=self._cfg.no_speech_threshold,
            log_prob_threshold=self._cfg.logprob_threshold,
            initial_prompt=self._cfg.initial_prompt,
            word_timestamps=True,
        )
        # The generator does the actual inference — drain it here, inside
        # the worker thread, not lazily on the event loop.
        segments: list[SegmentDict] = []
        for s in segments_iter:
            segments.append(
                {
                    "text": s.text,
                    "start": s.start,
                    "end": s.end,
                    "avg_logprob": s.avg_logprob,
                    "no_speech_prob": s.no_speech_prob,
                    "words": [
                        {
                            "word": w.word,
                            "start": w.start,
                            "end": w.end,
                            "probability": w.probability,
                        }
                        for w in (s.words or [])
                    ],
                }
            )
        return segments, info

    async def transcribe(self, tx: Transmission) -> Transcript:
        started = time.perf_counter()
        segments, info = await asyncio.to_thread(self._transcribe_sync, tx.pcm)
        latency_ms = round((time.perf_counter() - started) * 1000)

        text, words, avg_logprob, no_speech_prob = merge_segments(segments)
        logger.debug(
            "asr.transcribed",
            transmission_id=tx.id,
            engine=self.name,
            latency_ms=latency_ms,
            chars=len(text),
        )
        return Transcript(
            transmission_id=tx.id,
            engine=self.name,
            text=text,
            words=words,
            avg_logprob=avg_logprob,
            no_speech_prob=no_speech_prob,
            language=getattr(info, "language", None),
            latency_ms=latency_ms,
        )

    async def warmup(self) -> None:
        started = time.perf_counter()
        await asyncio.to_thread(
            self._transcribe_sync, np.zeros(_WARMUP_SAMPLES, dtype=np.float32)
        )
        logger.info(
            "asr.warmed_up",
            engine=self.name,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
