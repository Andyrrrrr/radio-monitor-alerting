"""Alert router: severity → channels, alert-once-then-update, dedupe.

The contract (docs/decisions.md D12): one incident produces ONE alert when
it opens, then in-place updates — a re-alert happens only on severity
escalation. Fifteen buzzes for one mayday trains people to silence the app.

Re-alerting until a human acknowledges is delivered by Pushover's emergency
priority (retry/expire on priority 2), not by router logic — one mechanism,
not two fighting each other (docs/decisions.md D7).

Channel failures are results, not exceptions: every attempt (including
failures) is returned for the alert delivery log, and one channel failing
never stops the others.
"""

from datetime import datetime, timedelta

import structlog

from vhfwatch.alerting.base import AlertChannel, LinkBuilder
from vhfwatch.alerting.console import ConsoleAlertChannel
from vhfwatch.alerting.macos import MacOSAlertChannel
from vhfwatch.alerting.pushover import PushoverAlertChannel
from vhfwatch.config import AlertingConfig
from vhfwatch.models import AlertResult, Incident, Severity

logger = structlog.get_logger(__name__)


class AlertRouter:
    def __init__(self, cfg: AlertingConfig, channels: dict[str, AlertChannel]) -> None:
        self._cfg = cfg
        self._channels = channels
        # (incident_id, severity) → when we last alerted; the dedupe key
        # includes severity so escalation always gets through.
        self._sent: dict[tuple[str, Severity], datetime] = {}

    def _channels_for(self, severity: Severity) -> list[AlertChannel]:
        names = {
            Severity.CRITICAL: self._cfg.channels_critical,
            Severity.URGENT: self._cfg.channels_urgent,
            Severity.WATCH: self._cfg.channels_watch,
            Severity.ROUTINE: self._cfg.channels_routine,
        }[severity]
        selected: list[AlertChannel] = []
        for name in names:
            channel = self._channels.get(name)
            if channel is None:
                logger.warning("alert.unknown_channel", channel=name)
            else:
                selected.append(channel)
        return selected

    async def dispatch(
        self, incident: Incident, is_new: bool, escalated: bool
    ) -> list[AlertResult]:
        """Send for a new or escalated incident; no-op otherwise.

        Returns every attempt's result so the caller can write the alert
        delivery log. Sets incident.alerted_at on the first real send.
        """
        if not (is_new or escalated):
            return []

        now = incident.last_activity_at
        key = (incident.id, incident.severity)
        last = self._sent.get(key)
        if last is not None and now - last < timedelta(
            seconds=self._cfg.dedupe_window_s
        ):
            logger.info(
                "alert.deduped",
                incident_id=incident.id,
                severity=incident.severity.value,
            )
            return []

        is_update = not is_new
        results: list[AlertResult] = []
        for channel in self._channels_for(incident.severity):
            try:
                result = await channel.send(incident, is_update)
            except Exception as e:  # noqa: BLE001 — one channel must not kill the rest
                logger.error(
                    "alert.channel_crashed", channel=channel.name, error=str(e)
                )
                result = AlertResult(channel=channel.name, ok=False, detail=str(e))
            results.append(result)
            logger.info(
                "alert.sent" if result.ok else "alert.failed",
                incident_id=incident.id,
                channel=channel.name,
                severity=incident.severity.value,
                is_update=is_update,
            )

        if results:
            self._sent[key] = now
            if incident.alerted_at is None and any(r.ok for r in results):
                incident.alerted_at = now
        elif is_new:
            # Severity routed to zero channels (e.g. ROUTINE) — fine, but
            # visible, so a misconfigured empty list can't hide.
            logger.info(
                "alert.no_channels",
                incident_id=incident.id,
                severity=incident.severity.value,
            )
        return results

    async def healthcheck(self) -> dict[str, bool]:
        return {
            name: await channel.healthcheck()
            for name, channel in self._channels.items()
        }


def create_channels(
    cfg: AlertingConfig, local_timezone: str, link_for: LinkBuilder
) -> dict[str, AlertChannel]:
    """Build every channel named anywhere in the routing config.

    Channels are constructed by name from config, never by platform
    sniffing (docs/conventions.md §5) — a Linux box that doesn't list
    "macos" never touches osascript.
    """
    wanted = set(
        cfg.channels_critical
        + cfg.channels_urgent
        + cfg.channels_watch
        + cfg.channels_routine
    )
    channels: dict[str, AlertChannel] = {}
    for name in wanted:
        if name == "console":
            channels[name] = ConsoleAlertChannel(local_timezone, link_for)
        elif name == "macos":
            channels[name] = MacOSAlertChannel(local_timezone)
        elif name == "pushover":
            channels[name] = PushoverAlertChannel(
                cfg.pushover, local_timezone, link_for
            )
        else:
            logger.warning("alert.unknown_channel", channel=name)
    return channels
