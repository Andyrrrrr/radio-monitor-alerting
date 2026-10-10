"""The outbox: delivery must survive a dead network without slowing listening.

Timings are tiny (milliseconds) so retries and backoff are exercised for real
rather than mocked.
"""

import asyncio
from collections.abc import Awaitable, Callable

from vhfwatch.alerting.outbox import OutageSummary, Outbox
from vhfwatch.models import AlertResult


def _ok() -> AlertResult:
    return AlertResult(channel="t", ok=True)


def _down() -> AlertResult:
    return AlertResult(channel="t", ok=False, detail="no route", retryable=True)


def _bad_token() -> AlertResult:
    return AlertResult(channel="t", ok=False, detail="401", retryable=False)


def make(**kw: object) -> Outbox:
    args: dict[str, object] = {
        "max_pending": 10,
        "retry_window_s": 5.0,
        "retry_initial_s": 0.01,
        "retry_max_s": 0.05,
        "pace_s": 0.0,
    }
    args.update(kw)
    return Outbox(**args)  # type: ignore[arg-type]


async def _run_until(
    outbox: Outbox, done: Callable[[], bool], timeout: float = 3.0
) -> None:
    worker = asyncio.create_task(outbox.run())
    try:
        async with asyncio.timeout(timeout):
            while not done():
                await asyncio.sleep(0.005)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def _sender(
    log: list[str], name: str, results: list[AlertResult]
) -> Callable[[], Awaitable[AlertResult]]:
    """Returns the scripted results in order, then OK forever."""
    queue = list(results)

    async def send() -> AlertResult:
        log.append(name)
        return queue.pop(0) if queue else _ok()

    return send


def test_delivers_in_submission_order() -> None:
    async def go() -> list[str]:
        log: list[str] = []
        ob = make()
        for n in ("a", "b", "c"):
            ob.submit(n, _sender(log, n, []))
        await _run_until(ob, lambda: ob.stats.delivered == 3)
        return log

    assert asyncio.run(go()) == ["a", "b", "c"]


def test_retries_through_an_outage_and_reports_the_gap() -> None:
    """The point of the whole thing: a wifi drop delays delivery but does not
    lose it, and the operator is told afterwards that it happened."""

    async def go() -> tuple[list[str], list[OutageSummary]]:
        log: list[str] = []
        summaries: list[OutageSummary] = []
        ob = make(on_recovered=summaries.append)
        ob.submit("a", _sender(log, "a", [_down(), _down(), _down()]))
        await _run_until(ob, lambda: ob.stats.delivered == 1)
        return log, summaries

    log, summaries = asyncio.run(go())
    assert log == ["a", "a", "a", "a"]  # three failures, then success
    assert len(summaries) == 1
    assert summaries[0].delivered_late == 1
    assert summaries[0].abandoned == 0


def test_permanent_failure_is_not_retried() -> None:
    """A rejected token fails identically forever. Retrying hides a config
    error behind a delay."""

    async def go() -> tuple[list[str], Outbox]:
        log: list[str] = []
        ob = make()
        ob.submit("a", _sender(log, "a", [_bad_token()]))
        await _run_until(ob, lambda: ob.stats.abandoned == 1)
        return log, ob

    log, ob = asyncio.run(go())
    assert log == ["a"]
    assert ob.stats.retried == 0


def test_gives_up_after_the_window_but_keeps_delivering_newer_items() -> None:
    async def go() -> tuple[list[str], Outbox]:
        log: list[str] = []
        ob = make(retry_window_s=0.05)
        ob.submit("stale", _sender(log, "stale", [_down()] * 100))
        ob.submit("fresh", _sender(log, "fresh", []))
        await _run_until(ob, lambda: ob.stats.delivered == 1)
        return log, ob

    log, ob = asyncio.run(go())
    assert ob.stats.abandoned == 1
    assert log[-1] == "fresh"


def test_overflow_drops_the_oldest_routine_item() -> None:
    async def go() -> tuple[list[str], Outbox]:
        log: list[str] = []
        ob = make(max_pending=2)
        # Submitted before the worker starts, so nothing drains in between.
        for n in ("a", "b", "c", "d"):
            ob.submit(n, _sender(log, n, []))
        await _run_until(ob, lambda: ob.stats.delivered == 2)
        return log, ob

    log, ob = asyncio.run(go())
    assert ob.stats.dropped == 2
    assert log == ["c", "d"]  # newest survive; the oldest were dropped


def test_urgent_item_interrupts_a_backoff_and_goes_first() -> None:
    """A distress alert must never wait behind a retry of a routine push."""

    async def go() -> list[str]:
        log: list[str] = []
        ob = make(retry_initial_s=0.5, retry_max_s=0.5)  # long backoff
        ob.submit("routine", _sender(log, "routine", [_down()]))
        worker = asyncio.create_task(ob.run())
        await asyncio.sleep(0.05)  # routine has failed and is now backing off
        ob.submit("MAYDAY", _sender(log, "MAYDAY", []), urgent=True)
        try:
            async with asyncio.timeout(3):
                while ob.stats.delivered < 2:
                    await asyncio.sleep(0.005)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        return log

    log = asyncio.run(go())
    # routine tried once and failed, the alert went out immediately, and the
    # routine item was resumed afterwards rather than lost.
    assert log == ["routine", "MAYDAY", "routine"]


def test_a_crashing_send_does_not_kill_the_worker() -> None:
    async def go() -> list[str]:
        log: list[str] = []
        ob = make()

        async def boom() -> AlertResult:
            raise RuntimeError("bug in a channel")

        ob.submit("boom", boom)
        ob.submit("after", _sender(log, "after", []))
        await _run_until(ob, lambda: ob.stats.delivered == 1)
        return log

    assert asyncio.run(go()) == ["after"]
