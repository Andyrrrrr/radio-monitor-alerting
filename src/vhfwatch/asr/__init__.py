"""ASR engines behind the Transcriber protocol, selected by config."""

from vhfwatch.asr.base import Transcriber
from vhfwatch.config import AsrConfig

__all__ = ["Transcriber", "create_transcriber"]


def create_transcriber(cfg: AsrConfig) -> Transcriber:
    """Build the configured engine, importing it only now.

    Lazy so a Linux box never imports mlx and a Mac without faster-whisper
    still runs the MLX path. A missing package is a setup problem, so the
    error says which extra to install instead of a bare ImportError.
    """
    if cfg.engine == "mlx":
        try:
            from vhfwatch.asr.mlx import MLXWhisperTranscriber
        except ImportError as e:
            raise RuntimeError(
                "asr.engine = 'mlx' but mlx-whisper is not installed — "
                'install with: uv pip install -e ".[dev,macos]" '
                "(Apple Silicon only)"
            ) from e
        return MLXWhisperTranscriber(cfg)
    if cfg.engine == "faster_whisper":
        try:
            from vhfwatch.asr.faster_whisper import (
                FasterWhisperTranscriber,
            )
        except ImportError as e:
            raise RuntimeError(
                "asr.engine = 'faster_whisper' but faster-whisper is not "
                'installed — install with: uv pip install -e ".[dev,linux]"'
            ) from e
        return FasterWhisperTranscriber(cfg)
    # Deepgram is documented as an optional cloud upgrade
    # (docs/architecture.md §5.3) but deliberately not built for the POC —
    # local-first is the constraint and nothing may require the network.
    raise RuntimeError(
        f"asr.engine = '{cfg.engine}' is not implemented in the POC; "
        "use 'mlx' (Apple Silicon) or 'faster_whisper' (portable)"
    )
