"""Web Push channel: urgent alerts on phones that saved the Marine 1 page to
the home screen (docs/plans/2026-11-21-home-screen-push-alerts.md).

A second path beside Telegram, not a replacement. It goes out through the
same router, dedupe and outbox as every other channel, and only for
incidents: health notices stay in Telegram so this sound always means a
possible emergency (the reasoning of D33).

How it works: each phone that taps "Turn on alerts" stores a subscription
(an endpoint at Apple's or Google's push service plus two keys). Sending is
one outbound HTTPS POST per phone, signed with our VAPID key and encrypted
for that phone, done by `pywebpush` (the standard implementation; this is
not encryption to write by hand). Nothing listens for inbound connections.

Secret is env-only: VHFWATCH_VAPID_PRIVATE_KEY, the raw P-256 private key in
base64url (generate with `python -m vhfwatch.alerting.webpush`).
"""

import asyncio
import base64
import json
import os
import sqlite3
from typing import Any, Protocol
from urllib.parse import urlsplit

import structlog
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from vhfwatch.alerting.base import LinkBuilder, format_body, format_title
from vhfwatch.config import WebPushConfig
from vhfwatch.models import AlertResult, Incident

logger = structlog.get_logger(__name__)

_KEY_ENV = "VHFWATCH_VAPID_PRIVATE_KEY"
_MAX_BODY = 300  # iOS shows about four lines; the record holds the rest


class PushStore(Protocol):
    """The two store calls this channel needs (store.Database has them)."""

    def list_push_subscriptions(self) -> list[sqlite3.Row]: ...

    def delete_push_subscription(self, endpoint: str) -> bool: ...


def load_vapid() -> Vapid02 | None:
    """The VAPID key from the environment, or None when not configured."""
    raw = os.environ.get(_KEY_ENV, "").strip()
    if not raw:
        return None
    return Vapid02.from_raw(raw.encode())


def public_key_b64(vapid: Vapid02) -> str:
    """The public key the browser needs to subscribe (applicationServerKey)."""
    point = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(point).rstrip(b"=").decode()


def vapid_subject(cfg: WebPushConfig, base_url: str) -> str:
    """The VAPID "sub" contact: the configured one, else this station's own
    https origin (`https://host`, no path, the only URL form the signing
    library accepts). Empty when neither is available."""
    if cfg.vapid_subject:
        return cfg.vapid_subject
    parts = urlsplit(base_url)
    if parts.scheme == "https" and parts.hostname:
        return f"https://{parts.hostname}"
    return ""


def send_one(
    vapid: Vapid02,
    cfg: WebPushConfig,
    subject: str,
    subscription: dict[str, Any],
    payload: dict[str, str],
) -> tuple[bool, bool, str]:
    """One push to one phone, blocking: (ok, gone, detail). `gone` means the
    push service says this subscription no longer exists (404/410)."""
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=vapid,
            # A fresh dict every send: pywebpush adds aud/exp to it.
            vapid_claims={"sub": subject},
            ttl=cfg.ttl_s,
            headers={"Urgency": "high"},
            timeout=10,
        )
        return True, False, "sent"
    except WebPushException as e:
        status = e.response.status_code if e.response is not None else None
        return False, status in (404, 410), f"HTTP {status}: {str(e)[:150]}"
    except Exception as e:  # noqa: BLE001 — network errors must not crash the router
        return False, False, f"{type(e).__name__}: {str(e)[:150]}"


def subscription_info(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "endpoint": row["endpoint"],
        "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
    }


class WebPushAlertChannel:
    name = "webpush"

    def __init__(
        self,
        cfg: WebPushConfig,
        local_timezone: str,
        link_for: LinkBuilder,
        store: PushStore,
        subject: str,
    ) -> None:
        self._cfg = cfg
        self._subject = subject
        self._tz = local_timezone
        self._link_for = link_for
        self._store = store
        self._vapid = load_vapid()
        if self._vapid is None:
            logger.warning(
                "alert.webpush_unconfigured",
                hint=f"set {_KEY_ENV} to send home-screen alerts",
            )
        if not subject:
            logger.warning(
                "alert.webpush_no_subject",
                hint="set [web] base_url to the https address, or vapid_subject",
            )

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        if self._vapid is None:
            return AlertResult(
                channel=self.name, ok=False, detail="VAPID key not configured"
            )
        if not self._subject:
            return AlertResult(
                channel=self.name, ok=False, detail="no VAPID subject configured"
            )
        subs = self._store.list_push_subscriptions()
        if not subs:
            # Nobody has turned alerts on: nothing to deliver, nothing to retry.
            logger.info("alert.webpush_no_subscribers", incident_id=incident.id)
            return AlertResult(
                channel=self.name, ok=True, detail="no phones subscribed"
            )
        payload = {
            "title": format_title(incident, is_update),
            "body": format_body(incident, self._tz, None)[:_MAX_BODY],
            "url": self._link_for(incident) or "/marine1",
            "tag": incident.id,  # an update replaces the earlier notification
        }
        vapid = self._vapid
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    send_one,
                    vapid,
                    self._cfg,
                    self._subject,
                    subscription_info(s),
                    payload,
                )
                for s in subs
            )
        )
        sent = 0
        for row, (ok, gone, detail) in zip(subs, results, strict=True):
            if ok:
                sent += 1
                continue
            if gone:
                self._store.delete_push_subscription(row["endpoint"])
                logger.info(
                    "alert.webpush_subscription_removed",
                    label=row["label"],
                    detail=detail,
                )
            else:
                logger.warning(
                    "alert.webpush_failed", label=row["label"], detail=detail
                )
        failed_live = sum(1 for ok, gone, _ in results if not ok and not gone)
        detail = f"sent to {sent} of {len(subs)} phone(s)"
        if sent == 0 and failed_live:
            # Every reachable phone failed: worth the outbox's retry.
            return AlertResult(
                channel=self.name, ok=False, detail=detail, retryable=True
            )
        return AlertResult(channel=self.name, ok=True, detail=detail)

    async def send_health(self, title: str, message: str) -> AlertResult:
        """Not used: health notices stay in Telegram (module docstring)."""
        return AlertResult(
            channel=self.name, ok=True, detail="health notices are not pushed"
        )

    async def healthcheck(self) -> bool:
        return self._vapid is not None and bool(self._subject)


def _generate_key() -> None:
    """`python -m vhfwatch.alerting.webpush`: print a new private key line for
    /etc/vhfwatch/vhfwatch.env. Keep it secret; changing it means every phone
    has to turn alerts on again."""
    vapid = Vapid02()
    vapid.generate_keys()
    raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    print(f"{_KEY_ENV}={base64.urlsafe_b64encode(raw).rstrip(b'=').decode()}")


if __name__ == "__main__":
    _generate_key()
