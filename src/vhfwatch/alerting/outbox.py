"""Outbox: delivery that can never slow down listening.

Why this exists. The station runs on wifi that drops. Sending a notification
used to be awaited inline in the pipeline, so during an outage every Telegram
upload would block for its full timeout *inside the transcription stage* —
real traffic would sit unprocessed behind a dead network, then be dropped when
the bounded queue filled. Retrying inside the send would only make that worse.

So delivery is decoupled, in the same spirit as "ingest never blocks"
(AGENTS.md): the pipeline `submit()`s and moves on; one background worker
delivers in order, retrying with exponential backoff while the network is down.

Behaviour, each with a reason:
- **FIFO, so a backlog arrives in the order it happened.** After an outage the
  operator reads the day back in sequence, not shuffled.
- **Urgent items jump the queue and interrupt a backoff wait.** A distress
  alert must never wait behind a retry of a routine transmission push.
- **Bounded.** On overflow the oldest routine item is dropped, loudly. An
  unbounded queue is a memory leak that only manifests during the outage.
- **Gives up after `retry_window_s`.** A notification hours old is noise.
  The recording is still in the archive, so nothing is lost, only not pushed.
- **Never retries a permanent failure.** A rejected token fails identically
  forever; retrying hides a configuration error behind a delay.
- **Reports the gap when it ends.** Delivery that silently fell behind is the
  quiet dishonesty the project exists to avoid, so on recovery one notice says
  how long it was down, how many arrived late and how many were abandoned.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from vhfwatch.models import AlertResult

logger = structlog.get_logger(__name__)

Send = Callable[[], Awaitable[AlertResult]]


@dataclass
class OutageSummary:
    """What happened while notifications were failing, for the recovery notice."""

    started_at: datetime
    ended_at: datetime
    delivered_late: int
    abandoned: int


@dataclass
class _Item:
    label: str
    send: Send
    submitted_at: float
    urgent: bool = False
    attempts: int = 0


@dataclass
class OutboxStats:
    delivered: int = 0
    abandoned: int = 0
    dropped: int = 0
    retried: int = 0
    pending: int = 0
    in_outage: bool = False
    extra: dict[str, int] = field(default_factory=dict)


class Outbox:
    def __init__(
        self,
        *,
        max_pending: int,
        retry_window_s: float,
        retry_initial_s: float,
        retry_max_s: float,
        pace_s: float = 1.1,
        on_recovered: Callable[[OutageSummary], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_pending = max_pending
        self._window = retry_window_s
        self._initial = retry_initial_s
        self._max_delay = retry_max_s
        # Telegram allows roughly one message per second to one chat; a
        # post-outage backlog sent flat out would just earn 429s.
        self._pace = pace_s
        self._on_recovered = on_recovered
        self._clock = clock

        self._urgent: deque[_Item] = deque()
        self._normal: deque[_Item] = deque()
        self._wake = asyncio.Event()  # something is waiting
        self._urgent_event = asyncio.Event()  # an urgent item is waiting

        self.stats = OutboxStats()
        self._busy = False
        self._outage_started: datetime | None = None
        self._outage_late = 0
        self._outage_abandoned = 0

    # -- producer side: never blocks -------------------------------------

    def submit(self, label: str, send: Send, *, urgent: bool = False) -> None:
        item = _Item(label, send, self._clock(), urgent=urgent)
        if urgent:
            self._urgent.append(item)
            self._urgent_event.set()
        else:
            if len(self._normal) >= self._max_pending:
                dropped = self._normal.popleft()
                self.stats.dropped += 1
                logger.warning(
                    "outbox.overflow_drop",
                    dropped=dropped.label,
                    max_pending=self._max_pending,
                    hint="notifications are backing up — the network has "
                    "probably been down for a while; recordings are archived",
                )
            self._normal.append(item)
        self._wake.set()

    @property
    def pending(self) -> int:
        return len(self._urgent) + len(self._normal)

    async def drain(self, timeout_s: float) -> bool:
        """Wait for everything pending to be delivered or given up on.

        Used at shutdown so a replayed file's notifications are not lost when
        the run ends. Bounded: a dead network must not hold up an exit.
        """
        try:
            async with asyncio.timeout(timeout_s):
                while self.pending or self._busy:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            return False
        return True

    # -- worker -----------------------------------------------------------

    async def run(self) -> None:
        """Deliver forever. Cancelled at pipeline shutdown."""
        while True:
            item = await self._next()
            self._busy = True
            try:
                await self._deliver(item)
            finally:
                self._busy = False
            self.stats.pending = self.pending
            if self.pending and self._pace > 0:
                await asyncio.sleep(self._pace)

    async def _next(self) -> _Item:
        while True:
            if self._urgent:
                item = self._urgent.popleft()
                if not self._urgent:
                    self._urgent_event.clear()
                return item
            if self._normal:
                return self._normal.popleft()
            self._wake.clear()
            await self._wake.wait()

    async def _deliver(self, item: _Item) -> None:
        delay = self._initial
        while True:
            item.attempts += 1
            try:
                result = await item.send()
            except Exception as e:  # noqa: BLE001 — a bug in one send must not kill the worker
                logger.error("outbox.send_crashed", label=item.label, error=str(e))
                self.stats.abandoned += 1
                return

            if result.ok:
                self._succeeded(item)
                return

            if not result.retryable:
                self._abandon(item, f"permanent failure: {result.detail}")
                return

            age = self._clock() - item.submitted_at
            if age + delay > self._window:
                self._abandon(item, f"gave up after {age:.0f}s: {result.detail}")
                return

            self._failed(item, result)
            self.stats.retried += 1
            if await self._sleep_unless_urgent(delay) and not item.urgent:
                # A distress alert arrived while we were backing off: send it
                # first, and resume this item afterwards.
                self._normal.appendleft(item)
                return
            delay = min(delay * 2, self._max_delay)

    async def _sleep_unless_urgent(self, delay: float) -> bool:
        """Sleep `delay` seconds; True if an urgent item woke us early."""
        if self._urgent:
            return True
        try:
            await asyncio.wait_for(self._urgent_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    # -- outage bookkeeping ----------------------------------------------

    def _failed(self, item: _Item, result: AlertResult) -> None:
        if self._outage_started is None:
            self._outage_started = datetime.now(UTC)
            self.stats.in_outage = True
            logger.warning(
                "outbox.outage_started",
                label=item.label,
                detail=result.detail,
                hint="notifications are being retried; listening continues",
            )

    def _succeeded(self, item: _Item) -> None:
        self.stats.delivered += 1
        # Logged because absence of a failure is not evidence of delivery: the
        # first live transmission through Telegram could only be confirmed from
        # the operator's phone. `attempts` > 1 and a large `queued_s` are the
        # signature of a wifi drop that recovered.
        logger.info(
            "outbox.delivered",
            label=item.label,
            attempts=item.attempts,
            queued_s=round(self._clock() - item.submitted_at, 1),
            urgent=item.urgent,
        )
        if item.attempts > 1 and self._outage_started is not None:
            self._outage_late += 1
        if self._outage_started is None:
            return
        summary = OutageSummary(
            started_at=self._outage_started,
            ended_at=datetime.now(UTC),
            delivered_late=self._outage_late,
            abandoned=self._outage_abandoned,
        )
        self._outage_started = None
        self._outage_late = 0
        self._outage_abandoned = 0
        self.stats.in_outage = False
        logger.info(
            "outbox.recovered",
            delivered_late=summary.delivered_late,
            abandoned=summary.abandoned,
        )
        if self._on_recovered is not None:
            self._on_recovered(summary)

    def _abandon(self, item: _Item, reason: str) -> None:
        self.stats.abandoned += 1
        if self._outage_started is not None:
            self._outage_abandoned += 1
        logger.warning(
            "outbox.abandoned",
            label=item.label,
            reason=reason,
            hint="not pushed; the recording is still archived",
        )
