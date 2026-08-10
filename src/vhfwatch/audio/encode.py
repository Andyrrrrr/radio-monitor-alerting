"""Audio archival: Opus for storage, AAC/M4A playback copy at ingest.

Opus at 16 kHz mono ~24 kbps is the archive; the AAC copy exists because
Opus-in-WebM is unreliable on iOS Safari and the crew is on iPhones
(docs/architecture.md §5.8). Both are produced at ingest — never transcode
in the request path.

The SHA-256 is computed over the archive file as written, so a record's
hash can be verified against the archived bytes at any later date
(docs/decisions.md D9).

ffmpeg does the encoding. Without ffmpeg (or with archive_codec = "wav")
this degrades to plain 16-bit WAV — bigger files, but never lost audio.
"""

import hashlib
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import structlog

from vhfwatch.config import StorageConfig
from vhfwatch.models import Transmission

logger = structlog.get_logger(__name__)

_ffmpeg_path: str | None | bool = False  # False = not looked up yet


def _ffmpeg() -> str | None:
    global _ffmpeg_path
    if _ffmpeg_path is False:
        _ffmpeg_path = shutil.which("ffmpeg")
        if _ffmpeg_path is None:
            logger.warning(
                "encode.ffmpeg_missing",
                hint="archiving as WAV — install ffmpeg for Opus archive "
                "and iPhone-playable AAC copies (brew install ffmpeg)",
            )
    return _ffmpeg_path if isinstance(_ffmpeg_path, str) else None


def archive_wav(tx: Transmission, audio_dir: Path) -> tuple[Path, str]:
    """Plain 16-bit WAV archive — the no-ffmpeg fallback."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / f"{tx.id}.wav"
    _write_wav(path, tx)
    return path, _sha256(path)


def archive_audio(
    tx: Transmission, audio_dir: Path, cfg: StorageConfig
) -> tuple[Path, str]:
    """Archive a transmission's PCM; returns (archive_path, sha256).

    Also writes the playback copy ({id}.m4a) beside the archive when
    encoding is available. Any encode failure falls back to WAV for that
    segment — losing fidelity of format, never the audio itself.
    """
    ffmpeg = _ffmpeg()
    if cfg.archive_codec == "wav" or ffmpeg is None:
        return archive_wav(tx, audio_dir)

    audio_dir.mkdir(parents=True, exist_ok=True)
    archive_path = audio_dir / f"{tx.id}.opus"
    playback_path = audio_dir / f"{tx.id}.m4a"

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _write_wav(tmp_path, tx)
        _run_ffmpeg(
            ffmpeg,
            tmp_path,
            archive_path,
            ["-c:a", "libopus", "-b:a", cfg.archive_bitrate],
        )
        try:
            _run_ffmpeg(
                ffmpeg,
                tmp_path,
                playback_path,
                ["-c:a", "aac", "-b:a", "48k"],
            )
        except subprocess.CalledProcessError as e:
            # Playback copy is a convenience; the archive is the record.
            logger.error(
                "encode.playback_copy_failed",
                transmission_id=tx.id,
                detail=e.stderr.decode(errors="replace")[-300:],
            )
        return archive_path, _sha256(archive_path)
    except (subprocess.CalledProcessError, OSError) as e:
        detail = (
            e.stderr.decode(errors="replace")[-300:]
            if isinstance(e, subprocess.CalledProcessError)
            else str(e)
        )
        logger.error(
            "encode.archive_failed_falling_back_to_wav",
            transmission_id=tx.id,
            detail=detail,
        )
        return archive_wav(tx, audio_dir)
    finally:
        tmp_path.unlink(missing_ok=True)


def playback_path_for(archive_path: Path) -> Path | None:
    """The AAC playback copy for an archive file, if one exists."""
    candidate = archive_path.with_suffix(".m4a")
    return candidate if candidate.exists() else None


def _write_wav(path: Path, tx: Transmission) -> None:
    ints = (np.clip(tx.pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(tx.sample_rate)
        wf.writeframes(ints.tobytes())


def _run_ffmpeg(ffmpeg: str, src: Path, dst: Path, codec_args: list[str]) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src), *codec_args, str(dst)],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
