"""Marine 1 page: the tides/weather cache (fresh, stale, offline back-off),
the page and its feed, and the trusted-networks access rule (D36)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pydantic
import pytest
from starlette.testclient import TestClient

from vhfwatch.config import DashboardConfig, Settings, WebConfig
from vhfwatch.models import Detection, Severity, Transcript, Transmission, new_id
from vhfwatch.store import Database
from vhfwatch.web.app import create_app
from vhfwatch.web.conditions import Conditions, next_tide

T0 = datetime(2026, 11, 14, 22, 0, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"

NOAA = {
    "predictions": [
        {"t": "2026-11-14 03:00", "v": "6.343", "type": "H"},
        {"t": "2026-11-14 07:37", "v": "4.601", "type": "L"},
        {"t": "2026-11-14 13:22", "v": "8.7", "type": "H"},
        {"t": "2026-11-14 20:50", "v": "0.044", "type": "L"},
    ]
}
FORECAST = {
    "properties": {
        "updated": "2026-11-14T12:37:00-08:00",
        "periods": [
            {
                "name": "Today",
                "detailedForecast": "E wind 5 to 10 kt. Waves 2 ft or less.",
            },
            {"name": "Tonight", "detailedForecast": "SE wind 5 to 10 kt."},
        ],
    }
}
ALERTS = {
    "features": [
        {
            "properties": {
                "event": "Small Craft Advisory",
                "headline": "Small Craft Advisory until 4 PM",
            }
        }
    ]
}


class Counter:
    def __init__(self) -> None:
        self.calls = 0


def good_transport(counter: Counter | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.calls += 1
        if "tidesandcurrents" in request.url.host:
            return httpx.Response(200, json=NOAA)
        if request.url.path.endswith("/forecast"):
            return httpx.Response(200, json=FORECAST)
        return httpx.Response(200, json=ALERTS)

    return httpx.MockTransport(handle)


def dead_transport(counter: Counter | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.calls += 1
        raise httpx.ConnectError("network unreachable", request=request)

    return httpx.MockTransport(handle)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


# -- conditions cache -------------------------------------------------------


def test_tides_parse_into_aware_local_times() -> None:
    c = Conditions(DashboardConfig(), TZ, transport=good_transport())
    got = asyncio.run(c.tides("9449211"))
    assert not got.stale and got.value is not None
    assert [e.kind for e in got.value] == ["High", "Low", "High", "Low"]
    assert got.value[2].height_ft == 8.7
    assert got.value[0].at.utcoffset() == timedelta(hours=-8)  # PST in November
    # 22:00 UTC is 14:00 PST: the next tide is the 20:50 low.
    upcoming = next_tide(got.value, T0)
    assert upcoming is not None and upcoming.kind == "Low"


def test_forecast_and_alerts_parse() -> None:
    c = Conditions(DashboardConfig(), TZ, transport=good_transport())
    got = asyncio.run(c.forecast())
    assert got.value is not None
    assert got.value.periods[0] == ("Today", "E wind 5 to 10 kt. Waves 2 ft or less.")
    assert got.value.alerts == ["Small Craft Advisory until 4 PM"]


def test_a_fresh_copy_is_not_fetched_again() -> None:
    calls, clock = Counter(), Clock()
    c = Conditions(DashboardConfig(), TZ, transport=good_transport(calls), clock=clock)
    asyncio.run(c.tides("9449211"))
    clock.now += timedelta(minutes=30)
    asyncio.run(c.tides("9449211"))
    assert calls.calls == 1


def test_offline_serves_the_last_copy_marked_stale() -> None:
    clock = Clock()
    cfg = DashboardConfig(tides_cache_s=60)
    c = Conditions(cfg, TZ, transport=good_transport(), clock=clock)
    asyncio.run(c.tides("9449211"))
    c._transport = dead_transport()  # the network goes away
    clock.now += timedelta(minutes=5)
    got = asyncio.run(c.tides("9449211"))
    assert got.stale and got.value is not None and len(got.value) == 4
    assert got.error is not None and "ConnectError" in got.error
    assert got.fetched_at == T0


def test_offline_with_nothing_cached_returns_no_value_not_an_exception() -> None:
    c = Conditions(DashboardConfig(), TZ, transport=dead_transport())
    got = asyncio.run(c.forecast())
    assert got.value is None and got.stale and got.error


def test_after_a_failure_it_waits_before_trying_again() -> None:
    calls, clock = Counter(), Clock()
    cfg = DashboardConfig(retry_after_failure_s=120)
    c = Conditions(cfg, TZ, transport=dead_transport(calls), clock=clock)
    asyncio.run(c.tides("9449211"))
    clock.now += timedelta(seconds=30)
    asyncio.run(c.tides("9449211"))  # inside the back-off: no new request
    assert calls.calls == 1
    clock.now += timedelta(seconds=120)
    asyncio.run(c.tides("9449211"))
    assert calls.calls == 2


def test_noaa_error_object_counts_as_a_failure() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "No data was found."}})

    c = Conditions(DashboardConfig(), TZ, transport=httpx.MockTransport(handle))
    got = asyncio.run(c.tides("0000000"))
    assert got.value is None and got.error is not None and "No data" in got.error


# -- the page -----------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.general.data_dir = tmp_path / "data"
    settings.general.data_dir.mkdir(parents=True)
    with Database(settings.general.data_dir / "vhfwatch.db") as db:
        db.migrate()
        for i, (text, severity) in enumerate(
            [
                (
                    "Pan-pan, sailing vessel Annabella, south entrance to Squalicum Harbor",
                    "urgent",
                ),
                (
                    "sail sail sail sail sail sail",
                    None,
                ),  # the hallucination gate rejects this
            ]
        ):
            tx_id = new_id()
            db.insert_transmission(
                Transmission(
                    id=tx_id,
                    channel="16",
                    started_at=T0 + timedelta(minutes=i),
                    ended_at=T0 + timedelta(minutes=i, seconds=4),
                    duration_ms=4000,
                    pcm=np.zeros(1, dtype=np.float32),
                    sample_rate=16000,
                    rms_dbfs=-20.0,
                    peak_dbfs=-6.0,
                    est_snr_db=40.0,
                    audio_path=None,
                    audio_sha256=None,
                    source="test",
                )
            )
            db.insert_transcript(
                Transcript(transmission_id=tx_id, engine="t", text=text)
            )
            if severity:
                db.insert_detection(
                    Detection(
                        id=new_id(),
                        transmission_id=tx_id,
                        severity=Severity(severity),
                        confidence=1.0,
                        matched_terms=["pan pan"],
                        tier="window",
                        classifier_output=None,
                        created_at=T0,
                    )
                )
    return settings


def client(
    cfg: Settings, host: str = "127.0.0.1", transport: httpx.MockTransport | None = None
) -> TestClient:
    conditions = Conditions(
        cfg.web.dashboard, TZ, transport=transport or good_transport()
    )
    return TestClient(create_app(cfg, conditions), client=(host, 51234))


def test_page_shows_feed_tides_weather_and_cams(cfg: Settings) -> None:
    with client(cfg) as c:
        page = c.get("/marine1")
    assert page.status_code == 200
    html = page.text
    assert "Squalicum Harbor" in html and "URGENT" in html  # the feed
    assert "Likely noise" in html  # the rejected transcript is shown, marked
    assert "8.7 ft" in html and "NOAA predictions" in html  # tides
    assert "Small Craft Advisory until 4 PM" in html  # NWS warning first
    assert "boatingcenter.org/webcams" in html  # webcam links
    assert 'target="_blank"' in html


def test_page_renders_offline_with_the_radio_feed_intact(cfg: Settings) -> None:
    with client(cfg, transport=dead_transport()) as c:
        page = c.get("/marine1")
    assert page.status_code == 200
    assert "Squalicum Harbor" in page.text
    assert "Tides unavailable" in page.text and "Forecast unavailable" in page.text


def test_feed_fragment_for_live_refresh(cfg: Settings) -> None:
    with client(cfg) as c:
        frag = c.get("/marine1/feed")
    assert frag.status_code == 200 and "Annabella" in frag.text
    assert "<html" not in frag.text


def test_unknown_tide_station_is_refused(cfg: Settings) -> None:
    with client(cfg) as c:
        assert c.get("/marine1?station=123").status_code == 400


# -- trusted networks (D36) ---------------------------------------------------


def test_tailscale_address_is_refused_unless_trusted(cfg: Settings) -> None:
    with client(cfg, host="100.101.102.103") as c:
        assert c.get("/marine1").status_code == 403


def test_tailscale_address_is_let_in_when_its_range_is_trusted(cfg: Settings) -> None:
    cfg.web.trusted_networks = ["100.64.0.0/10"]
    with client(cfg, host="100.101.102.103") as c:
        assert c.get("/marine1").status_code == 200
        assert c.get("/marine1/feed").status_code == 200
    with client(cfg, host="203.0.113.9") as c:  # outside every trusted range
        assert c.get("/marine1").status_code == 403


def test_trusting_the_whole_internet_is_rejected_at_load() -> None:
    with pytest.raises(pydantic.ValidationError, match="whole internet"):
        WebConfig(trusted_networks=["0.0.0.0/0"])


def test_a_malformed_network_is_rejected_at_load() -> None:
    with pytest.raises(pydantic.ValidationError):
        WebConfig(trusted_networks=["not-a-network"])
