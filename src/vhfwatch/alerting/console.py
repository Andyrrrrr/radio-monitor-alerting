"""Console alert channel — dev, always available, depends on nothing."""

import sys

import structlog

from vhfwatch.alerting.base import LinkBuilder, format_body, format_title
from vhfwatch.models import AlertResult, Incident

logger = structlog.get_logger(__name__)


class ConsoleAlertChannel:
    name = "console"

    def __init__(self, local_timezone: str, link_for: LinkBuilder) -> None:
        self._tz = local_timezone
        self._link_for = link_for

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        title = format_title(incident, is_update)
        body = format_body(incident, self._tz, self._link_for(incident))
        banner = "=" * 60
        # Deliberately print, not log: this IS the notification on a dev
        # box, and it must stand out from the log stream.
        print(f"\n{banner}\n{title}\n{body}\n{banner}\n", file=sys.stderr)
        return AlertResult(channel=self.name, ok=True)

    async def healthcheck(self) -> bool:
        return True
