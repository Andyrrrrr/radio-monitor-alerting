"""Pipeline entry point: audio source → segmenter → store.

Phase 0 shape: file source only, single sequential loop. Transcription,
detection, and the bounded inter-stage queues arrive with Phase 1; live
audio with Phase 2. The `run()` coroutine is source-agnostic on purpose —
when LiveAudioSource exists, nothing here changes but the source
construction (docs/architecture.md §5.1).

Usage:
    python -m vhfwatch.pipeline --source file --path tests/fixtures/sample.wav
"""

import argparse
import asyncio
from pathlib import Path

import structlog

from vhfwatch.audio.encode import archive_wav
from vhfwatch.audio.segmenter import Segmenter
from vhfwatch.audio.sources import AudioSource, FileAudioSource
from vhfwatch.config import Settings, load_config
from vhfwatch.log import configure_logging
from vhfwatch.models import Transmission
from vhfwatch.store import Database

logger = structlog.get_logger(__name__)


async def run(cfg: Settings, source: AudioSource) -> int:
    """Segment everything the source yields into the store.

    Returns the number of transmissions stored.
    """
    if source.sample_rate != cfg.audio.target_rate:
        # Resampling is a Phase 2 deliverable (native-rate capture → 16 kHz).
        # Until then, refuse loudly rather than segment at the wrong rate
        # with thresholds calibrated for another one.
        raise ValueError(
            f"{source.name} is {source.sample_rate} Hz but the pipeline expects "
            f"{cfg.audio.target_rate} Hz; resampling lands in Phase 2 — "
            "provide a 16 kHz file for now"
        )

    segmenter = Segmenter(
        cfg.segmenter,
        sample_rate=source.sample_rate,
        channel=cfg.general.channel,
        source_name=source.name,
    )
    data_dir = cfg.general.data_dir
    audio_dir = data_dir / "audio"
    data_dir.mkdir(parents=True, exist_ok=True)

    stored = 0
    with Database(data_dir / "vhfwatch.db") as db:
        db.migrate()
        async for frame in source.frames():
            for tx in segmenter.push(frame):
                _archive_and_store(db, tx, audio_dir)
                stored += 1
        tail = segmenter.flush()
        if tail is not None:
            _archive_and_store(db, tail, audio_dir)
            stored += 1
    await source.close()

    logger.info(
        "pipeline.finished",
        source=source.name,
        transmissions=stored,
        discarded=segmenter.discarded,
        noise_floor_db=(
            round(segmenter.noise_floor_db, 1)
            if segmenter.noise_floor_db is not None
            else None
        ),
    )
    return stored


def _archive_and_store(db: Database, tx: Transmission, audio_dir: Path) -> None:
    # Archive before insert: the row arrives complete, and the append-only
    # trigger never needs to allow a post-hoc audio_path update.
    path, sha256 = archive_wav(tx, audio_dir)
    tx.audio_path = str(path)
    tx.audio_sha256 = sha256
    db.insert_transmission(tx)
    logger.info(
        "transmission.stored",
        transmission_id=tx.id,
        duration_ms=tx.duration_ms,
        audio_path=str(path),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vhfwatch.pipeline",
        description="Segment a VHF audio stream into transmissions.",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    parser.add_argument(
        "--source",
        choices=["file", "live", "directory"],
        default=None,
        help="audio source kind (default: [audio].source from config)",
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="WAV file for --source file"
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    cfg = load_config(args.config)

    kind = args.source or cfg.audio.source
    if kind != "file":
        raise SystemExit(
            f"source '{kind}' is not implemented yet (live/directory land in "
            "Phase 2) — run with: --source file --path <file.wav>"
        )
    if args.path is None:
        parser.error("--source file requires --path")

    source = FileAudioSource(
        args.path,
        blocksize=cfg.audio.blocksize,
        channel=cfg.audio.use_channel,
    )
    asyncio.run(run(cfg, source))


if __name__ == "__main__":
    main()
