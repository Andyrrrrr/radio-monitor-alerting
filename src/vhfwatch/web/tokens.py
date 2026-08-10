"""Share tokens: per-recipient signed links from alerts to incident records.

Only the SHA-256 of a token is stored — a leaked database must not mint
working links (docs/architecture.md schema notes). The raw token exists
exactly once, inside the alert notification that carries it.

Shared by the pipeline (minting, at alert time) and the web app
(verification, at request time), so both sides hash identically.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import structlog

from vhfwatch.config import WebConfig
from vhfwatch.store import Database

logger = structlog.get_logger(__name__)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_share_link(
    db: Database, cfg: WebConfig, incident_id: str, recipient: str = "crew"
) -> str | None:
    """Create a share token and return the deep link for an alert.

    Returns None instead of raising: a failed link must never take the
    alert down with it — the offline-complete body still goes out.
    """
    try:
        raw = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(days=cfg.token_ttl_days)
        db.insert_share_token(incident_id, hash_token(raw), recipient, expires)
        return f"{cfg.base_url}/incidents/{incident_id}?token={raw}"
    except Exception as e:  # noqa: BLE001 — the alert must survive this
        logger.warning("share_token.mint_failed", incident_id=incident_id, error=str(e))
        return None


def verify_token(db: Database, raw: str) -> str | None:
    """Return the incident_id a token grants access to, or None.

    Constant-shape lookup by hash; expiry and revocation both checked.
    """
    row = db.get_share_token(hash_token(raw))
    if row is None:
        return None
    if row["revoked_at"] is not None:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if datetime.now(UTC) > expires:
        return None
    incident_id: str = row["incident_id"]
    return incident_id
