#!/usr/bin/env python
"""Push a recorded WAV (or corpus directory) through the full live pipeline.

Real ASR, real detection, real alerts — everything except the radio.
Paced to wall clock by default so it behaves like live input; --fast runs
flat out for batch runs.

    python scripts/replay.py data/corpus/20260810-193000Z-ch16.wav
    python scripts/replay.py data/corpus --fast
"""

import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path

from vhfwatch import pipeline
from vhfwatch.audio.sources import DirectoryAudioSource, FileAudioSource
from vhfwatch.config import load_config
from vhfwatch.log import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="WAV file or corpus directory")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--fast", action="store_true", help="run flat out instead of realtime"
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    configure_logging(args.log_level)
    cfg = load_config(args.config)

    if args.path.is_dir():
        # Corpus replay always runs flat out — pacing a day of recordings
        # to wall clock defeats the point.
        source: FileAudioSource | DirectoryAudioSource = DirectoryAudioSource(
            args.path, blocksize=cfg.audio.blocksize, channel=cfg.audio.use_channel
        )
    else:
        source = FileAudioSource(
            args.path,
            blocksize=cfg.audio.blocksize,
            channel=cfg.audio.use_channel,
            realtime=not args.fast,
        )

    stats = asyncio.run(pipeline.run(cfg, source))
    print("\nreplay finished:")
    for key, value in asdict(stats).items():
        if not key.startswith("_"):
            print(f"  {key:20} {value}")


if __name__ == "__main__":
    main()
