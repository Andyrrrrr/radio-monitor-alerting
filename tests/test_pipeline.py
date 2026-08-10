"""End-to-end pipeline: WAV in → transmissions, transcripts, detections,
incidents, and alerts out — with a fake transcriber (real ASR is exercised
on hardware via scripts/replay.py) and no network anywhere.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from tests.synth import BASE
from tests.test_alerting import RecordingChannel
from vhfwatch import pipeline
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.config import Settings
from vhfwatch.models import Transcript, Transmission
from vhfwatch.store import Database

FIXTURE = Path(__file__).parent / "fixtures" / "sample.wav"


class FakeTranscriber:
    """Returns scripted texts in order; loops the last one if exhausted."""

    name = "fake:test"

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.warmed_up = False

    async def transcribe(self, tx: Transmission) -> Transcript:
        text = self._texts.pop(0) if self._texts else "radio check"
        return Transcript(
            transmission_id=tx.id,
            engine=self.name,
            text=text,
            avg_logprob=-0.2,
            no_speech_prob=0.05,
        )

    async def warmup(self) -> None:
        self.warmed_up = True


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests must never hit the network: without the key the classifier is
    # disabled and detection runs Tier-1-only.
    monkeypatch.delenv("VHFWATCH_ANTHROPIC_API_KEY", raising=False)


def make_cfg(tmp_path: Path) -> Settings:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    return cfg


def run_pipeline(
    cfg: Settings, texts: list[str]
) -> tuple[pipeline.PipelineStats, FakeTranscriber, RecordingChannel]:
    source = FileAudioSource(FIXTURE, start_at=BASE)
    transcriber = FakeTranscriber(texts)
    channel = RecordingChannel("console")
    router = AlertRouter(
        cfg.alerting.model_copy(
            update={"channels_critical": ["console"], "channels_urgent": ["console"]}
        ),
        {"console": channel},
    )
    stats = asyncio.run(pipeline.run(cfg, source, transcriber, router))
    return stats, transcriber, channel


def test_full_chain_over_fixture(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    stats, transcriber, channel = run_pipeline(
        cfg,
        [
            "mayday mayday mayday this is vessel serenity taking on water",
            "vessel serenity coast guard copies your mayday",
        ],
    )

    # sample.wav contains two transmissions (see tests/make_fixtures.py).
    assert stats.transmissions == 2
    assert stats.transcripts == 2
    assert transcriber.warmed_up  # multi-second load must happen at startup

    # Both transmissions carry the same mayday conversation → one incident,
    # one alert (D12: one emergency, one notification).
    assert stats.detections == 2
    assert stats.incidents_opened == 1
    assert len(channel.sent) == 1

    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        rows = db.list_transmissions()
        assert len(rows) == 2
        for row in rows:
            audio = Path(row["audio_path"])
            assert audio.exists()
            # The stored hash must verify against the archived bytes —
            # the evidentiary property (docs/decisions.md D9).
            assert hashlib.sha256(audio.read_bytes()).hexdigest() == row["audio_sha256"]
            assert db.list_transcripts(row["id"]), "transcript row missing"

        incidents = db.list_incidents()
        assert len(incidents) == 1
        assert incidents[0]["severity"] == "critical"
        assert incidents[0]["alerted_at"] is not None
        alerts = db._conn.execute("SELECT * FROM alert").fetchall()
        assert len(alerts) == 1


def test_routine_traffic_produces_no_incident(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    stats, _, channel = run_pipeline(
        cfg,
        ["harbor control radio check", "loud and clear standing by"],
    )
    assert stats.transmissions == 2
    assert stats.detections == 0
    assert stats.incidents_opened == 0
    assert channel.sent == []


def test_hallucination_gate_blocks_detection(tmp_path: Path) -> None:
    # A Whisper artifact containing a watchword must be rejected before
    # matching — this is the exact failure hallucination.py exists for.
    cfg = make_cfg(tmp_path)

    class HallucinatingTranscriber(FakeTranscriber):
        async def transcribe(self, tx: Transmission) -> Transcript:
            return Transcript(
                transmission_id=tx.id,
                engine=self.name,
                text="mayday thank you for watching",
                avg_logprob=-0.2,
                no_speech_prob=0.05,
            )

    source = FileAudioSource(FIXTURE, start_at=BASE)
    channel = RecordingChannel("console")
    router = AlertRouter(cfg.alerting, {"console": channel})
    stats = asyncio.run(pipeline.run(cfg, source, HallucinatingTranscriber([]), router))
    assert stats.rejected_post_asr == 2
    assert stats.detections == 0
    assert channel.sent == []


def test_48k_file_is_resampled_not_refused(tmp_path: Path) -> None:
    # Phase 2: native-rate input resamples to 16 kHz instead of erroring.
    import numpy as np

    from tests.synth import write_wav16

    n = 48000 * 3
    t = np.arange(n, dtype=np.float32) / 48000
    pcm = (0.3 * np.sin(2 * np.pi * 400 * t)).astype(np.float32)
    pcm[: 48000 // 2] = 0.0  # leading quiet for the noise floor
    pcm[-48000:] = 0.0
    write_wav16(tmp_path / "hi.wav", pcm, rate=48000)

    cfg = make_cfg(tmp_path)
    source = FileAudioSource(tmp_path / "hi.wav", start_at=BASE)
    router = AlertRouter(cfg.alerting, {"console": RecordingChannel("console")})
    stats = asyncio.run(
        pipeline.run(cfg, source, FakeTranscriber(["radio check"]), router)
    )
    assert stats.transmissions == 1
