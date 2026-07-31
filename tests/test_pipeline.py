"""End-to-end over the committed fixture: WAV in → rows and audio files out.

This is the Phase 0 exit criterion as a test (docs/roadmap.md): the same
thing `python -m vhfwatch.pipeline --source file --path
tests/fixtures/sample.wav` does, pointed at a temp data dir.
"""

import asyncio
import hashlib
from pathlib import Path

from tests.synth import BASE
from vhfwatch import pipeline
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.config import Settings
from vhfwatch.store import Database

FIXTURE = Path(__file__).parent / "fixtures" / "sample.wav"


def test_pipeline_over_fixture(tmp_path: Path) -> None:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"

    source = FileAudioSource(FIXTURE, start_at=BASE)
    stored = asyncio.run(pipeline.run(cfg, source))

    # sample.wav contains two transmissions: one with a mid-sentence pause
    # (must not split) and one short-but-valid reply. See
    # tests/make_fixtures.py for the exact layout.
    assert stored == 2

    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        rows = db.list_transmissions()
    assert len(rows) == 2

    for row in rows:
        audio = Path(row["audio_path"])
        assert audio.exists()
        # The stored hash must verify against the archived bytes — that's
        # the evidentiary property (docs/decisions.md D9).
        assert hashlib.sha256(audio.read_bytes()).hexdigest() == row["audio_sha256"]
        assert row["source"] == "file:sample.wav"
        assert row["channel"] == "16"


def test_wrong_rate_file_refused(tmp_path: Path) -> None:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.audio.target_rate = 8000  # pretend the pipeline wants a different rate

    source = FileAudioSource(FIXTURE, start_at=BASE)
    try:
        asyncio.run(pipeline.run(cfg, source))
    except ValueError as e:
        assert "Phase 2" in str(e)
    else:
        raise AssertionError(
            "expected a loud refusal, not silent mis-rated segmentation"
        )
