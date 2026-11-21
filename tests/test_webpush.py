"""Home-screen alerts (Web Push): the subscription table, the alert channel
(success, a phone that is gone, network failure, nobody subscribed), the
router wiring, and the web endpoints that set it up."""

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pydantic
import pytest
from py_vapid import Vapid02
from pywebpush import WebPushException
from starlette.testclient import TestClient

import vhfwatch.alerting.webpush as wp
from vhfwatch.alerting.router import create_channels
from vhfwatch.config import AlertingConfig, Settings, WebPushConfig
from vhfwatch.models import Incident, Severity, new_id
from vhfwatch.store import Database
from vhfwatch.web.app import create_app

T0 = datetime(2026, 11, 21, 23, 0, tzinfo=UTC)


def _key() -> str:
    v = Vapid02()
    v.generate_keys()
    raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    k = _key()
    monkeypatch.setenv("VHFWATCH_VAPID_PRIVATE_KEY", k)
    return k


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "vhfwatch.db")
    d.migrate()
    return d


def _incident() -> Incident:
    return Incident(
        id=new_id(),
        opened_at=T0,
        last_activity_at=T0,
        closed_at=None,
        status="open",
        severity=Severity.URGENT,
        channels=["16"],
        detection_ids=[],
        summary=None,
        confidence=1.0,
        alerted_at=None,
        acknowledged_at=None,
        acknowledged_by=None,
    )


def _gone() -> WebPushException:
    return WebPushException("gone", response=SimpleNamespace(status_code=410, text=""))


# -- store ---------------------------------------------------------------------


def test_subscriptions_are_stored_replaced_and_removed(db: Database) -> None:
    db.upsert_push_subscription("https://push.example/a", "p1", "a1", "iPhone")
    db.upsert_push_subscription("https://push.example/a", "p2", "a2", "iPhone again")
    rows = db.list_push_subscriptions()
    assert len(rows) == 1 and rows[0]["p256dh"] == "p2"
    assert db.delete_push_subscription("https://push.example/a")
    assert db.list_push_subscriptions() == []


def test_migrating_twice_is_harmless(db: Database) -> None:
    db.migrate()  # the pipeline and the web app both migrate at start
    db.upsert_push_subscription("https://push.example/a", "p", "a", "x")
    assert len(db.list_push_subscriptions()) == 1


# -- channel -------------------------------------------------------------------


def _channel(db: Database) -> wp.WebPushAlertChannel:
    return wp.WebPushAlertChannel(
        WebPushConfig(),
        "America/Los_Angeles",
        lambda inc: f"https://x/i/{inc.id}",
        db,
        "https://station.example",
    )


def test_sends_to_every_phone(
    db: Database, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(wp, "webpush", lambda **kw: sent.append(kw))
    db.upsert_push_subscription("https://push.example/a", "p", "a", "one")
    db.upsert_push_subscription("https://push.example/b", "p", "a", "two")
    result = asyncio.run(_channel(db).send(_incident(), is_update=False))
    assert result.ok and result.detail == "sent to 2 of 2 phone(s)"
    assert len(sent) == 2
    assert sent[0]["headers"] == {"Urgency": "high"}
    assert '"url": "https://x/i/' in sent[0]["data"]  # tapping opens the record


def test_a_phone_that_is_gone_is_removed(
    db: Database, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(**kw: Any) -> None:
        if kw["subscription_info"]["endpoint"].endswith("/old"):
            raise _gone()

    monkeypatch.setattr(wp, "webpush", fake)
    db.upsert_push_subscription("https://push.example/old", "p", "a", "old phone")
    db.upsert_push_subscription("https://push.example/new", "p", "a", "new phone")
    result = asyncio.run(_channel(db).send(_incident(), is_update=False))
    assert result.ok
    assert [r["endpoint"] for r in db.list_push_subscriptions()] == [
        "https://push.example/new"
    ]


def test_network_failure_everywhere_is_retryable(
    db: Database, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(**kw: Any) -> None:
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(wp, "webpush", down)
    db.upsert_push_subscription("https://push.example/a", "p", "a", "one")
    result = asyncio.run(_channel(db).send(_incident(), is_update=False))
    assert not result.ok and result.retryable
    assert len(db.list_push_subscriptions()) == 1  # kept: it may work next time


def test_nobody_subscribed_is_not_a_failure(db: Database, key: str) -> None:
    result = asyncio.run(_channel(db).send(_incident(), is_update=False))
    assert result.ok and "no phones" in result.detail


def test_without_a_key_it_says_so(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VHFWATCH_VAPID_PRIVATE_KEY", raising=False)
    result = asyncio.run(_channel(db).send(_incident(), is_update=False))
    assert not result.ok and "not configured" in result.detail


def test_health_notices_are_never_pushed(
    db: Database, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[Any] = []
    monkeypatch.setattr(wp, "webpush", lambda **kw: called.append(kw))
    db.upsert_push_subscription("https://push.example/a", "p", "a", "one")
    result = asyncio.run(_channel(db).send_health("capture stalled", "check the cable"))
    assert result.ok and called == []


def test_router_builds_the_channel_only_with_a_store(db: Database, key: str) -> None:
    cfg = AlertingConfig(channels_critical=["webpush"], channels_urgent=["webpush"])
    assert "webpush" in create_channels(
        cfg, "UTC", lambda inc: None, push_store=db, push_subject="https://s.example"
    )
    assert "webpush" not in create_channels(cfg, "UTC", lambda inc: None)


# -- web endpoints -------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.general.data_dir = tmp_path / "data"
    settings.general.data_dir.mkdir(parents=True)
    return settings


def _client(cfg: Settings, host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(cfg), client=(host, 51234))


SUB = {
    "endpoint": "https://web.push.apple.com/abc",
    "keys": {"p256dh": "P", "auth": "A"},
}


def test_manifest_and_service_worker_are_served(cfg: Settings) -> None:
    with _client(cfg) as c:
        m = c.get("/manifest.webmanifest")
        sw = c.get("/sw.js")
        icon = c.get("/static/apple-touch-icon.png")
    assert m.json()["display"] == "standalone" and m.json()["start_url"] == "/marine1"
    assert sw.headers["service-worker-allowed"] == "/" and "showNotification" in sw.text
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def test_key_endpoint_reports_missing_setup(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VHFWATCH_VAPID_PRIVATE_KEY", raising=False)
    with _client(cfg) as c:
        assert c.get("/push/key").status_code == 503


def test_subscribe_then_send_a_test_to_that_phone(
    cfg: Settings, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(wp, "webpush", lambda **kw: sent.append(kw))
    with _client(cfg) as c:
        assert len(c.get("/push/key").json()["publicKey"]) > 80
        assert c.post(
            "/push/subscribe", json={"subscription": SUB, "label": "iPhone"}
        ).json()["ok"]
        res = c.post("/push/test", json={"endpoint": SUB["endpoint"]}).json()
    assert res["ok"] and len(sent) == 1
    assert sent[0]["subscription_info"]["endpoint"] == SUB["endpoint"]
    assert "test alert" in sent[0]["data"]


def test_bad_subscriptions_are_refused(cfg: Settings, key: str) -> None:
    with _client(cfg) as c:
        bad = {"endpoint": "http://not-secure/x", "keys": {"p256dh": "P", "auth": "A"}}
        assert c.post("/push/subscribe", json={"subscription": bad}).status_code == 400
        assert (
            c.post("/push/test", json={"endpoint": "https://nobody"}).status_code == 404
        )


def test_untrusted_callers_cannot_subscribe(cfg: Settings, key: str) -> None:
    with _client(cfg, host="203.0.113.9") as c:
        assert c.post("/push/subscribe", json={"subscription": SUB}).status_code == 403
        assert c.get("/push/key").status_code == 403


# -- the VAPID contact ("sub") and real signing (the 2026-11-21 test failure) ----


def test_contact_defaults_to_the_stations_own_https_origin() -> None:
    assert (
        wp.vapid_subject(WebPushConfig(), "https://vhfwatch.tail89b577.ts.net")
        == "https://vhfwatch.tail89b577.ts.net"
    )
    assert wp.vapid_subject(WebPushConfig(), "http://127.0.0.1:8080") == ""


def test_a_contact_url_with_a_path_is_refused_at_startup() -> None:
    # The library only accepts mailto: or https://host; a path failed at send time.
    with pytest.raises(pydantic.ValidationError, match="no path"):
        WebPushConfig(
            vapid_subject="https://github.com/Andyrrrrr/radio-monitor-alerting"
        )
    WebPushConfig(vapid_subject="https://github.com")  # bare host is fine


def test_real_signing_and_encryption_with_only_the_network_faked(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stand-in for webpush(): VAPID signing and payload encryption run for
    real against a freshly generated phone key; only the HTTP POST is faked."""
    import os

    import pywebpush
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    phone = ec.generate_private_key(ec.SECP256R1())
    p256dh = (
        base64.urlsafe_b64encode(
            phone.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        .rstrip(b"=")
        .decode()
    )
    auth = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
    posted: list[dict[str, Any]] = []

    def fake_post(url: str, **kw: Any) -> Any:
        posted.append({"url": url, **kw})
        return SimpleNamespace(status_code=201, text="", headers={})

    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    vapid = wp.load_vapid()
    assert vapid is not None
    ok, gone, detail = wp.send_one(
        vapid,
        WebPushConfig(),
        wp.vapid_subject(WebPushConfig(), "https://vhfwatch.tail89b577.ts.net"),
        {
            "endpoint": "https://web.push.apple.com/abc",
            "keys": {"p256dh": p256dh, "auth": auth},
        },
        {"title": "t", "body": "b", "url": "/marine1", "tag": "x"},
    )
    assert ok and not gone, detail
    assert posted and posted[0]["headers"]["Authorization"].startswith("vapid t=")


def test_no_contact_means_the_channel_says_so(db: Database, key: str) -> None:
    channel = wp.WebPushAlertChannel(WebPushConfig(), "UTC", lambda inc: None, db, "")
    db.upsert_push_subscription("https://push.example/a", "p", "a", "one")
    result = asyncio.run(channel.send(_incident(), is_update=False))
    assert not result.ok and "subject" in result.detail
