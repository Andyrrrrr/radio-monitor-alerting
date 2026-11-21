"""Tides and marine weather for the Marine 1 page, fetched and cached by the
web process (docs/plans/2026-11-21-marine1-web-dashboard.md).

Sources, both free and keyless, checked 2026-11-21:
- NOAA CO-OPS tide predictions (api.tidesandcurrents.noaa.gov), high/low
  times and heights in feet above MLLW, in the station's local time.
- NWS marine zone forecast and active alerts (api.weather.gov).

The rule that shapes this module: **the page must render with the network
down** (AGENTS.md constraint 5, and the radio feed beside these tiles never
needs the internet). So every fetch is cached; a failed fetch serves the last
good copy marked stale with its age; and after a failure there is a pause
(`retry_after_failure_s`) before the next attempt, so an offline host does
not make every page load wait out a timeout.

Nothing here is ours: the page labels tides as NOAA predictions and the
forecast as the National Weather Service's, and shows when each was fetched.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

import httpx
import structlog

from vhfwatch.config import DashboardConfig

logger = structlog.get_logger(__name__)

T = TypeVar("T")

_TIDES_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
_NWS = "https://api.weather.gov"


@dataclass
class TideEvent:
    at: datetime  # aware; NOAA sends station local time (lst_ldt)
    kind: str  # "High" | "Low"
    height_ft: float


@dataclass
class Forecast:
    updated: str | None  # NWS's own issue time, as sent
    periods: list[
        tuple[str, str]
    ]  # (name, text), e.g. ("Tonight", "SE wind 5 to 10 kt...")
    alerts: list[str]  # headline per active alert; empty when none


@dataclass
class Fetched(Generic[T]):
    """A cached value and how much to trust it."""

    value: T | None
    fetched_at: datetime | None
    stale: bool  # True when the latest attempt failed and this is an older copy
    error: str | None = None


@dataclass
class _Slot:
    value: Any = None
    fetched_at: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Conditions:
    def __init__(
        self,
        cfg: DashboardConfig,
        local_timezone: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = cfg
        # Every station in the zone keeps Pacific time, which is also the
        # station's display timezone ([general] local_timezone).
        self._tz = ZoneInfo(local_timezone)
        self._transport = transport  # tests inject httpx.MockTransport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._slots: dict[str, _Slot] = {}

    # -- public ------------------------------------------------------------

    async def tides(self, station_id: str) -> Fetched[list[TideEvent]]:
        return await self._cached(
            f"tides:{station_id}",
            self._cfg.tides_cache_s,
            lambda: self._fetch_tides(station_id),
        )

    async def forecast(self) -> Fetched[Forecast]:
        return await self._cached(
            f"forecast:{self._cfg.forecast_zone}",
            self._cfg.weather_cache_s,
            self._fetch_forecast,
        )

    # -- cache ---------------------------------------------------------------

    async def _cached(
        self, key: str, ttl_s: int, fetch: Callable[[], Awaitable[T]]
    ) -> Fetched[T]:
        slot = self._slots.setdefault(key, _Slot())
        async with slot.lock:  # one fetch per key at a time, however many readers
            now = self._clock()
            fresh = slot.fetched_at is not None and now - slot.fetched_at < timedelta(
                seconds=ttl_s
            )
            backing_off = slot.last_failure is not None and (
                now - slot.last_failure
                < timedelta(seconds=self._cfg.retry_after_failure_s)
            )
            if fresh:
                return Fetched(slot.value, slot.fetched_at, stale=False)
            if backing_off:
                return Fetched(
                    slot.value, slot.fetched_at, stale=True, error=slot.last_error
                )
            try:
                value = await fetch()
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                # A discard with a reason (docs/conventions.md §4): log it,
                # then serve the old copy, never an exception page.
                slot.last_failure = now
                slot.last_error = f"{type(e).__name__}: {e}"[:200]
                logger.warning(
                    "dashboard.fetch_failed", source=key, error=slot.last_error
                )
                return Fetched(
                    slot.value, slot.fetched_at, stale=True, error=slot.last_error
                )
            slot.value, slot.fetched_at = value, now
            slot.last_failure, slot.last_error = None, None
            return Fetched(value, now, stale=False)

    # -- sources -------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._cfg.fetch_timeout_s,
            headers={"User-Agent": self._cfg.user_agent},
            transport=self._transport,
        )

    async def _fetch_tides(self, station_id: str) -> list[TideEvent]:
        params = {
            "station": station_id,
            "product": "predictions",
            "datum": "MLLW",
            "time_zone": "lst_ldt",
            "interval": "hilo",
            "units": "english",
            "format": "json",
            # From local midnight, 48 h: today and tomorrow. ("date=today"
            # would ignore the range and stop at midnight.)
            "begin_date": self._clock().astimezone(self._tz).strftime("%Y%m%d"),
            "range": "48",
            "application": "vhf-watch",
        }
        async with self._client() as client:
            r = await client.get(_TIDES_URL, params=params)
            r.raise_for_status()
            data = r.json()
        if "error" in data:  # NOAA answers 200 with an error object
            raise ValueError(str(data["error"].get("message", data["error"])))
        return [
            TideEvent(
                at=datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=self._tz),
                kind="High" if p["type"] == "H" else "Low",
                height_ft=float(p["v"]),
            )
            for p in data["predictions"]
        ]

    async def _fetch_forecast(self) -> Forecast:
        zone = self._cfg.forecast_zone
        headers = {"Accept": "application/geo+json"}
        async with self._client() as client:
            fc = await client.get(
                f"{_NWS}/zones/forecast/{zone}/forecast", headers=headers
            )
            fc.raise_for_status()
            al = await client.get(
                f"{_NWS}/alerts/active", params={"zone": zone}, headers=headers
            )
            al.raise_for_status()
        props = fc.json()["properties"]
        periods = [(p["name"], p["detailedForecast"]) for p in props["periods"]]
        alerts = [
            f["properties"].get("headline") or f["properties"]["event"]
            for f in al.json()["features"]
        ]
        return Forecast(updated=props.get("updated"), periods=periods, alerts=alerts)


def next_tide(events: list[TideEvent], now: datetime) -> TideEvent | None:
    """The first high or low after `now` (both timezone-aware)."""
    return next((e for e in events if e.at > now), None)
