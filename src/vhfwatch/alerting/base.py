"""AlertChannel protocol and the notification body builder.

The body must be **fully actionable with zero connectivity** (docs/
architecture.md §5.7): cell coverage drops offshore, so severity, time,
vessel, nature, position, and a verbatim quote all ride in the message
itself. The incident page link is the rich layer, never the only path.
"""

from collections.abc import Callable
from datetime import UTC
from typing import Protocol
from zoneinfo import ZoneInfo

from vhfwatch.models import AlertResult, Incident


class AlertChannel(Protocol):
    name: str

    async def send(self, incident: Incident, is_update: bool) -> AlertResult: ...

    async def healthcheck(self) -> bool: ...


# Builds the per-incident deep link (usually minting a share token on the
# way). Injected so alerting never imports web code, and returning None —
# web app not running, token minting failed — just drops the link line
# while the offline-complete body still goes out.
LinkBuilder = Callable[[Incident], str | None]


def format_title(incident: Incident, is_update: bool) -> str:
    label = incident.severity.value.upper()
    prefix = "UPDATE" if is_update else "VHF ALERT"
    vessel = incident.summary.vessel_name if incident.summary else None
    return f"{prefix} [{label}]" + (f" {vessel}" if vessel else "")


def format_body(incident: Incident, local_timezone: str, link: str | None) -> str:
    """Plain-text notification body, offline-complete.

    Marine operations happen in local time; the database stays UTC
    (docs/conventions.md §3) — this is the presentation layer.
    """
    try:
        tz = ZoneInfo(local_timezone)
    except KeyError:
        tz = ZoneInfo("UTC")
    when = incident.last_activity_at.astimezone(tz).strftime("%H:%M:%S %Z")

    s = incident.summary
    lines = [
        (
            f"{incident.severity.value.upper()} at {when} — "
            f"ch {', '.join(incident.channels)} — "
            f"{len(incident.detection_ids)} transmission(s)"
        )
    ]
    if s is not None:
        if s.vessel_name:
            lines.append(f"Vessel: {s.vessel_name}")
        if s.vessel_description:
            lines.append(f"Description: {s.vessel_description}")
        if s.nature_of_emergency:
            lines.append(f"Nature: {s.nature_of_emergency}")
        if s.position_as_stated:
            lines.append(f"Position (as stated): {s.position_as_stated}")
        if s.latitude is not None and s.longitude is not None:
            lines.append(f"Coordinates: {s.latitude:.5f}, {s.longitude:.5f}")
        if s.persons_aboard is not None:
            lines.append(f"Persons aboard: {s.persons_aboard}")
        if s.injuries_reported:
            lines.append(f"Injuries: {s.injuries_reported}")
        lines.append(f'"{s.verbatim_quote}"')
    else:
        # LLM never ran — say so honestly rather than implying certainty.
        lines.append("(no classifier summary — raw transcript on the record)")
    utc = incident.last_activity_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    lines.append(f"UTC {utc}")
    if link:
        lines.append(link)
    return "\n".join(lines)
