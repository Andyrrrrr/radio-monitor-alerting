#!/usr/bin/env python
"""One Pushover notification per transmission, with transcript and audio link.

BRING-UP TOOL — NOT THE ALERT PATH. The real alert path (alerting/router.py)
fires on DETECTIONS: watchword or classifier hits, deduped and escalated. This
script deliberately ignores all of that and notifies on EVERY transmission, so
an operator comparing the rig against their own ear log gets a phone buzz they
can timestamp. Do not wire it into the pipeline and do not tune thresholds
against it.

Sends only after the transcript lands, or after --transcript-wait seconds if it
never does (gated before ASR, or rejected as a hallucination) — a transmission
the pipeline heard but did not transcribe is exactly what the operator needs to
know about, so it is still notified, with the reason left to the pipeline log.

The audio link needs a share token, and share tokens are scoped to incidents
(web/tokens.py). A per-transmission scope does not exist, so one session token
is minted against the most recent incident and reused: the audio route accepts
any valid token. That is a test-harness compromise, not a pattern to copy —
with no incident in the database, notifications go out without links.

Requires VHFWATCH_PUSHOVER_TOKEN and VHFWATCH_PUSHOVER_USER, and, for links to
open on a phone, the web app reachable at [web].base_url from the phone's
network.

    python scripts/notify_transmissions.py --config config/config.toml
"""

import argparse
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import structlog

from vhfwatch.config import Settings, load_config
from vhfwatch.log import configure_logging
from vhfwatch.store import Database
from vhfwatch.web.tokens import hash_token

logger = structlog.get_logger(__name__)

_API_URL = "https://api.pushover.net/1/messages.json"
_MAX_MESSAGE = 1024  # Pushover's cap
_MAX_TITLE = 250


def _mint_session_token(db: Database, cfg: Settings) -> str | None:
    """A single share token for the whole session, or None if impossible."""
    incidents = db.list_incidents(limit=1)
    if not incidents:
        logger.warning(
            "notify.no_incident_for_token",
            hint="no incident row to scope a share token to — "
            "notifications will go out without audio links",
        )
        return None
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=cfg.web.token_ttl_days)
    db.insert_share_token(
        incidents[0]["id"], hash_token(raw), "bringup-session", expires
    )
    return raw


def _format(
    row: sqlite3.Row, text: str | None, tz: str, channel: str
) -> tuple[str, str]:
    when = datetime.fromisoformat(row["started_at"]).astimezone(ZoneInfo(tz))
    title = f"VHF ch {channel} — {when.strftime('%H:%M:%S')}"
    snr = f"{row['est_snr_db']:.0f} dB" if row["est_snr_db"] is not None else "?"
    body = text.strip() if text else "(no transcript — see the pipeline log)"
    message = f"{body}\n\n{row['duration_ms'] / 1000:.1f}s · SNR {snr}"
    return title[:_MAX_TITLE], message[:_MAX_MESSAGE]


def _send(
    client: httpx.Client,
    token: str,
    user: str,
    title: str,
    message: str,
    url: str | None,
) -> None:
    data: dict[str, str | int] = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": 0,
    }
    if url:
        data["url"] = url
        data["url_title"] = "Play recording"
    try:
        response = client.post(_API_URL, data=data, timeout=10)
    except httpx.HTTPError as e:
        # A dead network must not kill the watcher: the pipeline is still
        # recording, and the operator can reconcile from the log afterwards.
        logger.warning("notify.failed", detail=str(e))
        return
    if response.status_code == 200:
        logger.info("notify.sent", title=title, text=message.splitlines()[0][:120])
    else:
        logger.warning(
            "notify.failed",
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument(
        "--transcript-wait",
        type=float,
        default=90.0,
        help="seconds to wait for a transcript before notifying without one",
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    configure_logging(args.log_level)
    cfg = load_config(args.config)

    token = os.environ.get("VHFWATCH_PUSHOVER_TOKEN")
    user = os.environ.get("VHFWATCH_PUSHOVER_USER")
    if not (token and user):
        raise SystemExit(
            "VHFWATCH_PUSHOVER_TOKEN and VHFWATCH_PUSHOVER_USER must be set"
        )

    with Database(cfg.general.data_dir / "vhfwatch.db") as db:
        share = _mint_session_token(db, cfg)
        # Start from now: this is a live aid, not a backfill.
        recent = db.list_transmissions(limit=1)
        cursor = recent[0]["id"] if recent else "0"
        pending: dict[str, tuple[sqlite3.Row, float]] = {}
        logger.info(
            "notify.started",
            base_url=cfg.web.base_url,
            links=bool(share),
            from_transmission=cursor,
        )
        with httpx.Client() as client:
            while True:
                for row in db.transmissions_after(cursor):
                    cursor = row["id"]
                    pending[row["id"]] = (row, time.monotonic())
                for tx_id, (row, first_seen) in list(pending.items()):
                    transcripts = db.list_transcripts(tx_id)
                    waited = time.monotonic() - first_seen
                    if not transcripts and waited < args.transcript_wait:
                        continue
                    text = transcripts[0]["text"] if transcripts else None
                    title, message = _format(
                        row, text, cfg.general.local_timezone, cfg.general.channel
                    )
                    url = (
                        f"{cfg.web.base_url}/transmissions/{tx_id}/audio?token={share}"
                        if share
                        else None
                    )
                    _send(client, token, user, title, message, url)
                    del pending[tx_id]
                time.sleep(args.poll_s)


if __name__ == "__main__":
    main()
