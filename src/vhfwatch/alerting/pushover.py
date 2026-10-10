"""Pushover alert channel — the real notification path for the POC.

Chosen over SMS deliberately (docs/decisions.md D7): A2P 10DLC registration
blocks for weeks; Pushover works in ten minutes, and its emergency priority
bypasses Do Not Disturb and re-alerts until acknowledged — which is also
how "escalation until someone acks" is delivered without router complexity.

Secrets are env-only: VHFWATCH_PUSHOVER_TOKEN, VHFWATCH_PUSHOVER_USER.
"""

import os

import httpx
import structlog

from vhfwatch.alerting.base import LinkBuilder, format_body, format_title
from vhfwatch.config import PushoverConfig
from vhfwatch.models import AlertResult, Incident, Severity

logger = structlog.get_logger(__name__)

_API_URL = "https://api.pushover.net/1/messages.json"
_TOKEN_ENV = "VHFWATCH_PUSHOVER_TOKEN"
_USER_ENV = "VHFWATCH_PUSHOVER_USER"

# Pushover caps: message 1024, title 250.
_MAX_MESSAGE = 1024
_MAX_TITLE = 250


class PushoverAlertChannel:
    name = "pushover"

    def __init__(
        self,
        cfg: PushoverConfig,
        local_timezone: str,
        link_for: LinkBuilder,
    ) -> None:
        self._cfg = cfg
        self._tz = local_timezone
        self._link_for = link_for
        self._token = os.environ.get(_TOKEN_ENV)
        self._user = os.environ.get(_USER_ENV)
        if not (self._token and self._user):
            logger.warning(
                "alert.pushover_unconfigured",
                hint=f"set {_TOKEN_ENV} and {_USER_ENV} to enable push alerts",
            )

    def _priority(self, incident: Incident) -> int:
        if incident.severity is Severity.CRITICAL:
            return self._cfg.priority_critical
        if incident.severity is Severity.URGENT:
            return self._cfg.priority_urgent
        return 0

    async def send_health(self, title: str, message: str) -> AlertResult:
        """Operational notice, never an incident (D12).

        Priority 0: a health notice must reach the phone, but it must not
        bypass Do Not Disturb the way a mayday does — an operator who is
        woken at 03:00 for a dead USB cable stops trusting the alerts that
        matter.
        """
        if not (self._token and self._user):
            return AlertResult(
                channel=self.name, ok=False, detail="credentials not configured"
            )
        return await self._post(
            {
                "token": self._token,
                "user": self._user,
                "title": title[:_MAX_TITLE],
                "message": message[:_MAX_MESSAGE],
                "priority": 0,
            }
        )

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        if not (self._token and self._user):
            return AlertResult(
                channel=self.name, ok=False, detail="credentials not configured"
            )
        priority = self._priority(incident)
        data: dict[str, str | int] = {
            "token": self._token,
            "user": self._user,
            "title": format_title(incident, is_update)[:_MAX_TITLE],
            "message": format_body(incident, self._tz, None)[:_MAX_MESSAGE],
            "priority": priority,
        }
        link = self._link_for(incident)
        if link:
            data["url"] = link
            data["url_title"] = "Open incident record"
        if priority == 2:
            # Emergency: re-alert every retry_s until acked or expire_s.
            data["retry"] = self._cfg.retry_s
            data["expire"] = self._cfg.expire_s

        return await self._post(data)

    async def _post(self, data: dict[str, str | int]) -> AlertResult:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(_API_URL, data=data)
            if response.status_code == 200:
                return AlertResult(
                    channel=self.name, ok=True, detail=response.text[:200]
                )
            detail = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.warning("alert.pushover_failed", detail=detail)
            return AlertResult(
                channel=self.name,
                ok=False,
                detail=detail,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        except httpx.HTTPError as e:
            # Network down is an expected condition, not a crash: the
            # console/macos channels still fired, and the failure is logged
            # to the alert table for the record.
            logger.warning("alert.pushover_failed", detail=str(e))
            return AlertResult(
                channel=self.name, ok=False, detail=str(e), retryable=True
            )

    async def healthcheck(self) -> bool:
        return bool(self._token and self._user)
