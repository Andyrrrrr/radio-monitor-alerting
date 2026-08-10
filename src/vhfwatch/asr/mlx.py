"""mlx-whisper transcriber — the Apple Silicon development default.

`mlx-whisper` is materially faster than CPU alternatives on M-series
(docs/conventions.md §5). Imported lazily so machines without the [macos]
extra can still import the package; you only pay for the engine you select.
"""

import asyncio
import time
from typing import Any

import numpy as np
import structlog

from vhfwatch.asr.base import merge_segments
from vhfwatch.config import AsrConfig
from vhfwatch.models import Transcript, Transmission

logger = structlog.get_logger(__name__)

# One second of silence, enough to force the model load and one full
# inference pass — the multi-second cost we refuse to pay on a real mayday.
_WARMUP_SAMPLES = 16000


def _hf_repo(model: str) -> str:
    """Map a bare Whisper model name to the mlx-community conversion.

    A name containing "/" is taken as an explicit HF repo and passed through,
    so nonstandard conversions remain reachable from config alone.
    """
    if "/" in model:
        return model
    return f"mlx-community/whisper-{model}-mlx"


class MLXWhisperTranscriber:
    def __init__(self, cfg: AsrConfig) -> None:
        import mlx_whisper

        self._mlx_whisper = mlx_whisper
        self._cfg = cfg
        self._repo = _hf_repo(cfg.model)
        self.name = f"mlx-whisper:{cfg.model}"

    def _transcribe_sync(self, pcm: np.ndarray) -> dict[str, Any]:
        # No beam_size: mlx-whisper decodes greedily. beam_size in config
        # applies to the faster-whisper engine only.
        result: dict[str, Any] = self._mlx_whisper.transcribe(
            pcm,
            path_or_hf_repo=self._repo,
            temperature=self._cfg.temperature,
            condition_on_previous_text=self._cfg.condition_on_previous_text,
            no_speech_threshold=self._cfg.no_speech_threshold,
            logprob_threshold=self._cfg.logprob_threshold,
            initial_prompt=self._cfg.initial_prompt,
            word_timestamps=True,
            verbose=None,
        )
        return result

    async def transcribe(self, tx: Transmission) -> Transcript:
        started = time.perf_counter()
        # to_thread: inference would otherwise stall the event loop — and
        # with it the audio ingest heartbeat — for seconds at a time.
        result = await asyncio.to_thread(self._transcribe_sync, tx.pcm)
        latency_ms = round((time.perf_counter() - started) * 1000)

        text, words, avg_logprob, no_speech_prob = merge_segments(
            result.get("segments", [])
        )
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
            language=result.get("language"),
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
