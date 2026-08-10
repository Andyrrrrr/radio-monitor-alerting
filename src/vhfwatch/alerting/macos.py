"""macOS notification channel via osascript — zero-setup dev alerting.

Platform-specific by nature, so it's selected by config like every other
channel (docs/conventions.md §5) — nothing imports it unless configured.
"""

import asyncio
import shutil

import structlog

from vhfwatch.alerting.base import format_body, format_title
from vhfwatch.models import AlertResult, Incident

logger = structlog.get_logger(__name__)


class MacOSAlertChannel:
    name = "macos"

    def __init__(self, local_timezone: str) -> None:
        # No link: notification banners can't carry a tappable URL anyway.
        self._tz = local_timezone

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        title = format_title(incident, is_update)
        # Notification banners truncate anyway; keep the essential line.
        body = format_body(incident, self._tz, None).splitlines()[0]
        script = (
            f"display notification {_applescript_str(body)} "
            f'with title {_applescript_str(title)} sound name "Sosumi"'
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                detail = stderr.decode().strip()
                logger.warning("alert.macos_failed", detail=detail)
                return AlertResult(channel=self.name, ok=False, detail=detail)
            return AlertResult(channel=self.name, ok=True)
        except (OSError, TimeoutError) as e:
            logger.warning("alert.macos_failed", detail=str(e))
            return AlertResult(channel=self.name, ok=False, detail=str(e))

    async def healthcheck(self) -> bool:
        return shutil.which("osascript") is not None


def _applescript_str(text: str) -> str:
    """Quote for AppleScript: backslash-escape, never interpolate raw."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
