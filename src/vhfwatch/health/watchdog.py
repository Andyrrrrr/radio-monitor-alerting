"""Stall detection and the periodic self-test (docs/architecture.md §5.9).

Everything here is deliberately plain functions and a tiny tracker class, so
it can be unit-tested without a radio, a clock or a network.

The shared rule, from D24: when something is stuck, **exit non-zero and let
the supervisor restart us**. In-process recovery is more code, more states,
and more ways to half-work — and half-working is the dangerous state because
it looks healthy.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta


class PipelineStalled(RuntimeError):
    """Base for "something stopped making progress" — the process must exit."""


class CaptureStalled(PipelineStalled):
    """No audio frames arrived within `[health].capture_stall_s`.

    Raised so the process exits non-zero and the supervisor restarts it.
    Live sources only: a file source legitimately ends.
    """


class StageStalled(PipelineStalled):
    """A processing stage has been inside one call for too long.

    ASR is the realistic culprit: the faster-whisper comparison (D25) saw
    9 s to decode 1.9 s of audio on a hallucination loop, and a model that
    hangs outright would otherwise freeze transcription with the process
    still alive and every other check green.
    """


class StageTracker:
    """Records when each stage entered a call, so a hung one can be seen.

    Why "busy for too long" rather than "no output recently": on a channel
    this quiet a healthy stage produces nothing for hours, so silence proves
    nothing. A stage stuck *inside a call* is the unambiguous signal.
    """

    def __init__(self) -> None:
        self._busy: dict[str, float] = {}

    @contextmanager
    def busy(self, stage: str) -> Iterator[None]:
        self._busy[stage] = time.monotonic()
        try:
            yield
        finally:
            self._busy.pop(stage, None)

    def stalled(self, limit_s: float) -> list[tuple[str, float]]:
        now = time.monotonic()
        return [
            (stage, now - since)
            for stage, since in self._busy.items()
            if now - since > limit_s
        ]


# -- daily self-test --------------------------------------------------------


def self_test_due(now_local: datetime, last_run: date | None, hour: int) -> bool:
    """True once per local day, at or after `hour`.

    `last_run` comes from the database (a `self_test` health_event), not from
    memory: a process that restarts at 14:00 must not send a second "daily"
    check because it forgot it already sent one at 09:00.
    """
    if now_local.hour < hour:
        return False
    return last_run != now_local.date()


def should_announce_start(
    last_started: datetime | None, now: datetime, quiet_for: timedelta
) -> bool:
    """Throttle the "started" notice.

    Under `Restart=always` a crash loop would otherwise send one every ten
    seconds, burying the notice that matters under the ones that don't.
    """
    return last_started is None or now - last_started > quiet_for


@dataclass
class SelfTestFacts:
    site: str
    channel: str
    uptime_s: float
    transmissions_24h: int
    last_transmission_local: str | None
    noise_floor_dbfs: float | None
    calibrated_floor_dbfs: float
    disk_free_gb: float
    asr_engine: str
    outbox_delivered: int
    outbox_abandoned: int
    outbox_dropped: int
    queue_drops: int


def format_self_test(f: SelfTestFacts) -> tuple[str, str]:
    """The daily "alive" message.

    On a channel this quiet, *no news* is indistinguishable from *dead*, so
    the absence of traffic has to be reported as a fact, not left as silence.
    Sending it through the real delivery path is also the end-to-end test of
    that path: if this arrives, notifications work.
    """
    hours, rem = divmod(int(f.uptime_s), 3600)
    uptime = (
        f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{hours}h {rem // 60}m"
    )
    floor = (
        f"{f.noise_floor_dbfs:.1f} dBFS (calibrated {f.calibrated_floor_dbfs:.1f})"
        if f.noise_floor_dbfs is not None
        else "not yet measured"
    )
    last = f.last_transmission_local or "none on record"
    lines = [
        f"{f.site}, ch {f.channel}, up {uptime}.",
        f"Transmissions in the last 24 h: {f.transmissions_24h}. Last: {last}.",
        f"Noise floor: {floor}.",
        f"Disk free: {f.disk_free_gb:.0f} GB. ASR: {f.asr_engine}.",
        (
            f"Notifications: {f.outbox_delivered} delivered, "
            f"{f.outbox_abandoned} abandoned, {f.outbox_dropped} dropped."
        ),
    ]
    if f.queue_drops:
        lines.append(f"⚠️ {f.queue_drops} segments dropped since start (ASR behind).")
    if f.transmissions_24h == 0:
        lines.append("No traffic is normal for this channel; audio is flowing.")
    return "vhf-watch: daily check, alive", "\n".join(lines)
