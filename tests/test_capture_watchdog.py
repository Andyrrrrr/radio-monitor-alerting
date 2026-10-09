"""The capture watchdog: a stalled audio source must kill the process.

Regression test for 2026-09-23, when the USB interface vanished, the
pipeline stayed alive and logged nothing for 50 minutes, and a dead capture
chain was indistinguishable from a quiet channel (docs/bringup-log.md).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from tests.test_alerting import RecordingChannel
from tests.test_pipeline import FakeTranscriber
from vhfwatch import pipeline
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.config import Settings
from vhfwatch.models import AudioFrame


class _LiveSource:
    """A live source yielding `n` silent frames, then optionally stalling.

    `name = "live"` is load-bearing: the watchdog must not arm for file
    sources, which legitimately end.
    """

    def __init__(self, n: int, then_stall: bool) -> None:
        self.name = "live"
        self.sample_rate = 16000
        self._n = n
        self._stall = then_stall
        self.closed = False

    async def frames(self) -> AsyncIterator[AudioFrame]:
        for _ in range(self._n):
            yield AudioFrame(
                pcm=np.zeros(1600, dtype=np.float32),
                sample_rate=self.sample_rate,
                captured_at=datetime.now(UTC),
                source_name=self.name,
            )
            await asyncio.sleep(0.02)
        if self._stall:
            await asyncio.sleep(3600)

    async def close(self) -> None:
        self.closed = True


def _cfg(tmp_path: Path, stall_s: int) -> Settings:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.health.capture_stall_s = stall_s
    return cfg


def test_stalled_capture_raises_and_closes_source(tmp_path: Path) -> None:
    source = _LiveSource(n=3, then_stall=True)
    with pytest.raises(BaseExceptionGroup) as excinfo:
        asyncio.run(pipeline.run(_cfg(tmp_path, 1), source, FakeTranscriber([])))

    assert excinfo.value.subgroup(pipeline.CaptureStalled) is not None
    assert source.closed  # shutdown still runs; no leaked stream


def test_quiet_channel_is_not_a_stall(tmp_path: Path) -> None:
    """Silence is quiet FRAMES, not an absence of frames.

    Ch 16 is quiet for hours at a time (0.3-1.4 transmissions/hour), so a
    watchdog that fired on silence would be useless.
    """
    source = _LiveSource(n=60, then_stall=False)
    stats = asyncio.run(pipeline.run(_cfg(tmp_path, 1), source, FakeTranscriber([])))

    assert stats.transmissions == 0  # silence produces no segments
    assert source.closed


def test_file_source_end_is_not_a_stall(tmp_path: Path) -> None:
    """A file source ends by design; the watchdog must never arm for it."""

    class _Fileish(_LiveSource):
        def __init__(self) -> None:
            super().__init__(n=5, then_stall=False)
            self.name = "file:fixture.wav"

    source = _Fileish()
    asyncio.run(pipeline.run(_cfg(tmp_path, 1), source, FakeTranscriber([])))
    assert source.closed


def _silence_cfg(tmp_path: Path, hours: int) -> Settings:
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.health.capture_stall_s = 60  # not under test here
    cfg.health.no_audio_alert_hours = hours
    cfg.alerting.channels_health = ["console"]
    return cfg


def _run_with_health(
    cfg: Settings, source: _LiveSource, monkeypatch: pytest.MonkeyPatch
) -> RecordingChannel:
    """Drive the ticker fast enough to observe, with a recording channel."""
    monkeypatch.setattr(pipeline, "_TICK_INTERVAL_S", 0.05)
    channel = RecordingChannel("console")
    router = AlertRouter(cfg.alerting, {"console": channel})
    asyncio.run(pipeline.run(cfg, source, FakeTranscriber([]), router))
    return channel


def test_no_transmission_alert_fires_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A radio powered off, tuned away, or with its antenna knocked loose
    still delivers perfect frames of silence — invisible to the capture
    watchdog, which is why this check exists separately (D24)."""
    cfg = _silence_cfg(tmp_path, hours=0)  # 0 = alert on the first tick
    source = _LiveSource(n=40, then_stall=False)
    channel = _run_with_health(cfg, source, monkeypatch)

    # Many ticks elapse, but a silent spell is ONE notice, not one per tick.
    assert len(channel.health) == 1
    title, message = channel.health[0]
    assert "nothing heard" in title.lower()
    # It must point at the radio, not the computer: the capture chain is
    # provably alive if we got here.
    assert "antenna" in message.lower()


def test_no_alert_before_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ch 16 is quiet for hours at a time. Six hours of silence is normal;
    firing early would train the operator to ignore the alert."""
    cfg = _silence_cfg(tmp_path, hours=6)
    source = _LiveSource(n=40, then_stall=False)
    channel = _run_with_health(cfg, source, monkeypatch)

    assert channel.health == []
