"""Marine 1 page: the tides/weather cache (fresh, stale, offline back-off),
the page and its feed, and the trusted-networks access rule (D36)."""

import asyncio
import itertools
import re
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
from vhfwatch.web.app import _today_and_tonight, create_app
from vhfwatch.web.conditions import Conditions, TideEvent, next_tide, tide_curve

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
        cfg.web.dashboard, TZ, transport=transport or good_transport(), clock=lambda: T0
    )
    return TestClient(create_app(cfg, conditions), client=(host, 51234))


def test_page_shows_feed_tides_weather_and_cams(cfg: Settings) -> None:
    with client(cfg) as c:
        page = c.get("/marine1")
    assert page.status_code == 200
    html = page.text
    assert "Squalicum Harbor" in html and "URGENT" in html  # the feed
    assert "Likely noise" in html  # the rejected transcript is shown, marked
    assert '8.7, "High"' in html and "NOAA predictions" in html  # tides (chart data)
    assert "Small Craft Advisory until 4 PM" in html  # NWS warning first
    assert "boatingcenter.org/webcams" in html  # webcam links
    assert "youtube.com/watch?v=6j0aasCRk_k" in html  # the operators' picks
    assert "wsdot.com" not in html and "windfinder" not in html  # removed 2026-11-21
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


# -- tide chart ---------------------------------------------------------------


def _events() -> list[TideEvent]:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(TZ)
    return [
        TideEvent(datetime(2026, 11, 21, 13, 22, tzinfo=tz), "High", 8.70),
        TideEvent(datetime(2026, 11, 21, 20, 50, tzinfo=tz), "Low", 0.04),
        TideEvent(datetime(2026, 11, 22, 4, 6, tzinfo=tz), "High", 7.31),
    ]


def test_curve_passes_through_the_predicted_highs_and_lows() -> None:
    ev = _events()
    pts = dict(
        tide_curve(ev, ev[0].at, ev[2].at - timedelta(minutes=1), timedelta(minutes=1))
    )
    assert pts[ev[0].at] == pytest.approx(8.70)
    assert pts[ev[1].at] == pytest.approx(0.04)
    halfway = ev[0].at + (ev[1].at - ev[0].at) / 2
    assert pts[halfway] == pytest.approx((8.70 + 0.04) / 2)  # mid-tide is the average


def test_curve_falls_steadily_from_high_to_low() -> None:
    ev = _events()
    heights = [h for _, h in tide_curve(ev, ev[0].at, ev[1].at, timedelta(minutes=10))]
    assert all(a >= b for a, b in itertools.pairwise(heights))


def test_curve_does_not_guess_outside_the_known_highs_and_lows() -> None:
    ev = _events()
    before = tide_curve(
        ev,
        ev[0].at - timedelta(hours=3),
        ev[0].at - timedelta(minutes=1),
        timedelta(minutes=10),
    )
    assert before == []


def test_page_carries_the_chart_data_without_the_list_or_drag_hint(
    cfg: Settings,
) -> None:
    with client(cfg) as c:
        html = c.get("/marine1").text
    assert "window.TIDE = " in html and 'id="tide-chart"' in html
    assert '"tz": "America/Los_Angeles"' in html
    assert "Highs and lows as a list" not in html  # removed 2026-11-21
    assert "Drag along the chart" not in html
    assert "estimated" in html  # but the reader is still told the curve is an estimate


def test_no_chart_when_tides_are_unavailable(cfg: Settings) -> None:
    with client(cfg, transport=dead_transport()) as c:
        html = c.get("/marine1").text
    assert 'id="tide-chart"' not in html and "window.TIDE = " not in html
    assert "Tides unavailable" in html


def test_tides_then_weather_then_radio_then_webcams(cfg: Settings) -> None:
    with client(cfg) as c:
        html = c.get("/marine1").text
    order = [html.index(f'id="{a}"') for a in ("tides", "weather", "ch16", "cams")]
    assert order == sorted(order)
    assert (
        "Next tide," not in html
    )  # the old summary card is gone; the chart replaces it


def test_transmissions_read_like_the_telegram_messages(cfg: Settings) -> None:
    with client(cfg) as c:
        html = c.get("/marine1/feed").text
    # Same metadata line as the Telegram caption (14:00 PST for 22:00 UTC).
    # Non-breaking spaces keep "SNR 40 dB" on one line on a phone.
    assert "ch 16 · 14:00:00 · 4.0s · SNR\u00a040\u00a0dB" in html
    assert 'class="day-chip"' in html and "Saturday, November 14" in html
    assert html.count('class="day-chip"') == 1  # one chip per day, not per message
    assert 'class="msg noise"' in html  # the rejected one is still marked


def _add_tx(cfg: Settings, text: str, minutes: int) -> None:
    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        tx_id = new_id()
        db.insert_transmission(
            Transmission(
                id=tx_id,
                channel="16",
                started_at=T0 + timedelta(minutes=minutes),
                ended_at=T0 + timedelta(minutes=minutes, seconds=4),
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
        db.insert_transcript(Transcript(transmission_id=tx_id, engine="t", text=text))


def test_a_spoken_position_gets_a_map_line_like_telegram(cfg: Settings) -> None:
    _add_tx(
        cfg, "aground at 48 degrees 44 minutes north 122 degrees 30 minutes west", 5
    )
    with client(cfg) as c:
        html = c.get("/marine1/feed").text
    assert "48.7333°N 122.5000°W (read from the transcript" in html
    assert "Open in Google Maps" in html
    assert "google.com/maps/search/?api=1&amp;query=48.733333,-122.500000" in html


def test_position_outside_the_expected_area_is_flagged_not_dropped(
    cfg: Settings,
) -> None:
    cfg.position.expected_area = [48.4, 49.0, -123.3, -122.4]
    _add_tx(cfg, "position 47 degrees 30 minutes north 122 degrees 20 minutes west", 5)
    with client(cfg) as c:
        html = c.get("/marine1/feed").text
    assert "47.5000°N" in html and "probably misheard" in html


def test_long_transcripts_are_marked_for_folding(cfg: Settings) -> None:
    with client(cfg) as c:
        html = c.get("/marine1/feed").text
    assert 'class="msg-text clamp"' in html and 'class="msg-more"' in html


def test_forecast_shows_today_and_tonight_in_the_daytime() -> None:
    periods = [("Today", "a"), ("Tonight", "b"), ("Sun", "c"), ("Sun Night", "d")]
    assert _today_and_tonight(periods) == [("Today", "a"), ("Tonight", "b")]
    assert (
        _today_and_tonight([("This Afternoon", "a"), ("Tonight", "b"), ("Sun", "c")])[
            -1
        ][0]
        == "Tonight"
    )


def test_forecast_shows_only_tonight_in_the_evening() -> None:
    # After dark NWS starts at "Tonight"; the next period is already tomorrow.
    assert _today_and_tonight([("Tonight", "b"), ("Sun", "c")]) == [("Tonight", "b")]
    assert _today_and_tonight([("Overnight", "b"), ("Sun", "c")]) == [
        ("Overnight", "b")
    ]
    assert _today_and_tonight([]) == []


def test_page_shows_no_later_forecast_periods(cfg: Settings) -> None:
    with client(cfg) as c:
        html = c.get("/marine1").text
    assert "<strong>Today:</strong>" in html and "<strong>Tonight:</strong>" in html


def test_only_the_newest_feed_visible_show_before_show_more(cfg: Settings) -> None:
    cfg.web.dashboard.feed_visible = 1
    with client(cfg) as c:
        page = c.get("/marine1").text
        frag = c.get("/marine1/feed").text
    assert 'id="feed-more"' in page
    for html in (page, frag):  # the live refresh marks them the same way
        classes = re.findall(r'<div class="(msg[^"]*)" data-id=', html)
        assert len(classes) == 2
        assert "extra" not in classes[0] and "extra" in classes[1]


def test_default_shows_eight_before_show_more() -> None:
    assert DashboardConfig().feed_visible == 8 and DashboardConfig().feed_limit == 40


def test_source_line_and_tagline_are_gone_but_the_footer_disclaimer_stays(
    cfg: Settings,
) -> None:
    with client(cfg) as c:
        html = c.get("/marine1").text
    assert "supplemental awareness — not a safety system" not in html
    assert "NOAA predictions, feet above" not in html
    assert (
        "Never rely on" in html and "primary alerting path" in html
    )  # AGENTS.md: keep it


def test_stale_tides_still_say_so(cfg: Settings) -> None:
    clock = Clock()
    conditions = Conditions(
        cfg.web.dashboard.model_copy(update={"tides_cache_s": 60}),
        TZ,
        transport=good_transport(),
        clock=clock,
    )
    app_client = TestClient(create_app(cfg, conditions), client=("127.0.0.1", 51234))
    with app_client as c:
        c.get("/marine1")  # warm the cache
        conditions._transport = dead_transport()
        clock.now += timedelta(minutes=5)
        html = c.get("/marine1").text
    assert "Tides could not refresh" in html


def test_alerts_box_hides_once_on_with_a_link_to_reopen_it(cfg: Settings) -> None:
    with client(cfg) as c:
        html = c.get("/marine1").text
    assert 'id="alerts-card"' in html and 'id="alerts-link"' in html
    assert "Alert settings for this phone" in html
    assert "card.hidden = true; link.hidden = false;" in html  # alerts on: box hidden
