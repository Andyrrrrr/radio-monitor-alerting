"""The capture watchdog: a stalled audio source must kill the process.

Regression test for 2026-09-23, when the USB interface vanished, the
pipeline stayed alive and logged nothing for 50 minutes, and a dead capture
chain was indistinguishable from a quiet channel (docs/bringup-log.md).
"""

import asyncio
import os
import signal
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from tests.synth import BASE
from tests.test_alerting import RecordingChannel
from tests.test_pipeline import FakeTranscriber
from vhfwatch import pipeline
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.config import Settings
from vhfwatch.health.watchdog import self_test_due, should_announce_start
from vhfwatch.models import AlertResult, AudioFrame

FIXTURE = Path(__file__).parent / "fixtures" / "sample.wav"


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
    # (A live source also sends "started" and the daily check; those are
    # asserted separately.)
    silence = [h for h in channel.health if "nothing heard" in h[0].lower()]
    assert len(silence) == 1
    title, message = silence[0]
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

    assert [h for h in channel.health if "nothing heard" in h[0].lower()] == []


class RecordingAudioChannel(RecordingChannel):
    """A channel that can carry audio, like Telegram."""

    def __init__(self, name: str = "telegram") -> None:
        super().__init__(name)
        self.audio: list[tuple[str, str]] = []

    async def send_audio(self, audio: object, caption: str) -> AlertResult:
        self.audio.append((str(audio), caption))
        return AlertResult(channel=self.name, ok=True)


def test_rejected_transcripts_are_notified_but_labelled(tmp_path: Path) -> None:
    """The bring-up script this replaces pushed a hallucination to the
    operator's phone with no sign the pipeline had thrown it out. A rejection
    must still reach the phone — it is evidence the radio heard something —
    but it must say so."""
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.alerting.notify_every_transmission = True
    cfg.alerting.channels_transmission = ["telegram"]
    channel = RecordingAudioChannel()
    router = AlertRouter(cfg.alerting, {"telegram": channel})

    source = FileAudioSource(FIXTURE, start_at=BASE)
    # A repeated token is what the gate exists to catch (D25).
    texts = ["! ! ! ! ! ! !", "mayday mayday this is vessel serenity"]
    asyncio.run(pipeline.run(cfg, source, FakeTranscriber(texts), router))

    assert len(channel.audio) == 2
    rejected = [c for _, c in channel.audio if "rejected" in c]
    assert len(rejected) == 1
    assert "hallucination gate" in rejected[0]
    # The clean one carries the transcript and no warning.
    clean = [c for _, c in channel.audio if "rejected" not in c]
    assert "vessel serenity" in clean[0]


def test_transmission_notifications_are_off_by_default(tmp_path: Path) -> None:
    """Sending audio off-box is opt-in: it is a monitoring aid, not the
    distress path, and it must never switch itself on."""
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    channel = RecordingAudioChannel()
    router = AlertRouter(cfg.alerting, {"telegram": channel})

    source = FileAudioSource(FIXTURE, start_at=BASE)
    asyncio.run(pipeline.run(cfg, source, FakeTranscriber(["radio check"]), router))

    assert channel.audio == []


def test_live_start_sends_one_notice_and_a_daily_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a power cut the "started" notice is the only sign the system came
    back by itself; and on a channel this quiet the daily check is the only way
    "nothing happened" is distinguishable from "nothing works"."""
    cfg = _silence_cfg(tmp_path, hours=6)
    cfg.health.self_test_hour = 0  # due immediately, whatever the time
    channel = _run_with_health(cfg, _LiveSource(n=40, then_stall=False), monkeypatch)

    titles = [t for t, _ in channel.health]
    assert any("started" in t for t in titles)
    daily = [m for t, m in channel.health if "daily check" in t]
    assert len(daily) == 1
    # It reports quiet as a fact, not as silence.
    assert "No traffic is normal" in daily[0]


def test_daily_check_is_once_per_day_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last run comes from the database, so a process that restarts at 14:00
    does not send a second "daily" check because it forgot the 09:00 one."""
    cfg = _silence_cfg(tmp_path, hours=6)
    cfg.health.self_test_hour = 0
    first = _run_with_health(cfg, _LiveSource(n=40, then_stall=False), monkeypatch)
    second = _run_with_health(cfg, _LiveSource(n=40, then_stall=False), monkeypatch)

    assert len([t for t, _ in first.health if "daily check" in t]) == 1
    assert [t for t, _ in second.health if "daily check" in t] == []


def test_restart_loop_does_not_spam_start_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Restart=always a crash loop would otherwise send a notice every ten
    seconds and bury the one that matters."""
    cfg = _silence_cfg(tmp_path, hours=6)
    cfg.health.self_test_hour = 24  # never due; isolate the start notice
    first = _run_with_health(cfg, _LiveSource(n=40, then_stall=False), monkeypatch)
    second = _run_with_health(cfg, _LiveSource(n=40, then_stall=False), monkeypatch)

    assert len([t for t, _ in first.health if "started" in t]) == 1
    assert [t for t, _ in second.health if "started" in t] == []


def test_a_hung_stage_kills_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASR stuck inside one call leaves the process alive and every other check
    green; the only honest response is to exit and be restarted (D24)."""

    class HangingTranscriber(FakeTranscriber):
        async def transcribe(self, tx: object) -> object:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    class LingeringSource:
        """Real audio, then silence forever — like a live source, which never
        ends. (A finite source ending stands the watchdog down on purpose.)"""

        name = "live"
        sample_rate = 16000

        def __init__(self) -> None:
            self._inner = FileAudioSource(FIXTURE, start_at=BASE)
            self.sample_rate = self._inner.sample_rate

        async def frames(self) -> AsyncIterator[AudioFrame]:
            async for frame in self._inner.frames():
                yield frame
            await asyncio.sleep(3600)

        async def close(self) -> None:
            await self._inner.close()

    cfg = _cfg(tmp_path, stall_s=60)
    cfg.health.heartbeat_timeout_s = 1
    with pytest.raises(BaseExceptionGroup) as excinfo:
        asyncio.run(
            asyncio.wait_for(
                pipeline.run(cfg, LingeringSource(), HangingTranscriber([])),
                timeout=30,
            )
        )
    assert excinfo.value.subgroup(pipeline.StageStalled) is not None


class FlakyAudioChannel(RecordingAudioChannel):
    """Fails retryably N times, like a wifi drop, then recovers."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self._failures = failures
        self.attempts = 0

    async def send_audio(self, audio: object, caption: str) -> AlertResult:
        self.attempts += 1
        if self._failures > 0:
            self._failures -= 1
            return AlertResult(
                channel=self.name, ok=False, detail="no route", retryable=True
            )
        return await super().send_audio(audio, caption)


def test_a_dead_network_does_not_block_transcription(tmp_path: Path) -> None:
    """The reason the outbox exists. Notifications used to be awaited inline in
    the transcription stage, so a wifi outage would stall real traffic behind
    a dead network. Here every send fails several times, and the pipeline must
    still transcribe everything — then the notifications must still arrive."""
    cfg = Settings()
    cfg.general.data_dir = tmp_path / "data"
    cfg.alerting.notify_every_transmission = True
    cfg.alerting.channels_transmission = ["telegram"]
    cfg.alerting.retry_initial_s = 0.01
    cfg.alerting.retry_max_s = 0.02
    channel = FlakyAudioChannel(failures=3)
    router = AlertRouter(cfg.alerting, {"telegram": channel})

    source = FileAudioSource(FIXTURE, start_at=BASE)
    stats = asyncio.run(
        pipeline.run(
            cfg, source, FakeTranscriber(["radio check", "radio check"]), router
        )
    )

    assert stats.transcripts == 2  # listening was never blocked
    assert channel.attempts > 2  # it really did retry
    assert len(channel.audio) == 2  # and nothing was lost


def test_self_test_due_boundaries() -> None:
    def at(h: int, m: int = 0) -> datetime:
        return datetime(2026, 10, 9, h, m, tzinfo=UTC)

    assert not self_test_due(at(8, 59), None, 9)  # before the hour
    assert self_test_due(at(9), None, 9)  # first run ever
    assert self_test_due(at(9), date(2026, 10, 8), 9)  # yesterday's does not count
    assert not self_test_due(at(9), date(2026, 10, 9), 9)  # already sent today
    assert self_test_due(at(23), None, 9)  # a late start still sends


def test_start_notice_throttle() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    quiet = timedelta(minutes=10)
    assert should_announce_start(None, now, quiet)
    assert not should_announce_start(now - timedelta(minutes=2), now, quiet)
    assert should_announce_start(now - timedelta(minutes=30), now, quiet)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_shutdown_works_even_when_sigint_is_ignored(
    tmp_path: Path, sig: signal.Signals
) -> None:
    """A Python process started with `cmd &` from a script inherits SIGINT as
    IGNORED, so it never raises KeyboardInterrupt and `kill -INT` does nothing.
    That left a stale pipeline running beside a new one on 2026-10-09. Shutdown
    must work however the process was launched — and for SIGTERM, which is what
    `systemctl stop` sends, it must be CLEAN rather than a mid-write kill."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # the background case
    try:

        async def go() -> None:
            loop = asyncio.get_running_loop()
            loop.call_later(0.5, os.kill, os.getpid(), sig)
            await asyncio.wait_for(
                pipeline.run_until_signalled(
                    _cfg(tmp_path, stall_s=60),
                    _LiveSource(n=3, then_stall=True),
                    FakeTranscriber([]),
                ),
                timeout=15,
            )

        asyncio.run(go())  # returns cleanly; a hang here would hit the timeout
    finally:
        signal.signal(signal.SIGINT, previous)
