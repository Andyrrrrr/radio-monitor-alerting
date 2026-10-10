"""Telegram bot channel — the one carrier that puts audio and transcript in
the same notification.

Chosen 2026-10-09 (docs/decisions.md D26) after the transcripts proved
unreliable enough that the operator needs to hear the recording beside the
text, every time. Pushover cannot do this: it attaches images only, so the
audio has to live behind a link, which fails on cell data.

What Telegram gives us that matters here:
- `sendAudio` carries a **caption**, so one message = recording + transcript,
  playable inline with a scrubber.
- Outbound HTTPS only. No inbound ports, no Tailscale, no exposing recordings
  on the public internet.
- Files are tiny: an Opus clip of a 10 s transmission is ~30 KB.

Secrets are env-only: VHFWATCH_TELEGRAM_TOKEN, VHFWATCH_TELEGRAM_CHAT_ID.

**Scope limit, deliberate:** audio leaves the building when this is enabled.
Marine Ch 16 is public broadcast traffic and the operator has accepted that.
The fire/EMS audio in data/corpus-fire/ is NOT — addresses and patient details
are plausible there (docs/status.json). Never point this channel at a rig
monitoring anything but marine.
"""

import os
from pathlib import Path

import httpx
import structlog

from vhfwatch.alerting.base import LinkBuilder, format_body, format_title
from vhfwatch.config import TelegramConfig
from vhfwatch.models import AlertResult, Incident

logger = structlog.get_logger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_TOKEN_ENV = "VHFWATCH_TELEGRAM_TOKEN"
_CHAT_ENV = "VHFWATCH_TELEGRAM_CHAT_ID"
# Telegram caps a caption at 1024 characters and a plain message at 4096.
_MAX_CAPTION = 1024
_MAX_MESSAGE = 4096


class TelegramAlertChannel:
    name = "telegram"

    def __init__(
        self, cfg: TelegramConfig, local_timezone: str, link_for: LinkBuilder
    ) -> None:
        self._cfg = cfg
        self._tz = local_timezone
        self._link_for = link_for
        self._token = os.environ.get(_TOKEN_ENV)
        self._chat = os.environ.get(_CHAT_ENV)
        if not (self._token and self._chat):
            logger.warning(
                "alert.telegram_unconfigured",
                hint=f"set {_TOKEN_ENV} and {_CHAT_ENV} to enable Telegram",
            )

    # -- incident and health notices -------------------------------------

    async def send(self, incident: Incident, is_update: bool) -> AlertResult:
        link = self._link_for(incident)
        title = format_title(incident, is_update)
        body = format_body(incident, self._tz, link)
        return await self._call(
            "sendMessage", data={"text": f"{title}\n\n{body}"[:_MAX_MESSAGE]}
        )

    async def send_health(self, title: str, message: str) -> AlertResult:
        return await self._call(
            "sendMessage",
            data={
                "text": f"{title}\n\n{message}"[:_MAX_MESSAGE],
                # Health notices are not emergencies: deliver quietly so the
                # ones that do ring keep meaning something.
                "disable_notification": self._cfg.silent_health,
            },
        )

    # -- the reason this channel exists ----------------------------------

    async def send_audio(self, audio: Path, caption: str) -> AlertResult:
        """One message: the recording, with the transcript as its caption.

        Falls back to text if the file is missing — a transcript with no
        audio still beats no notification at all.
        """
        if not audio.exists():
            logger.warning("alert.telegram_audio_missing", path=str(audio))
            return await self._call(
                "sendMessage", data={"text": caption[:_MAX_MESSAGE]}
            )
        return await self._call(
            "sendAudio",
            data={"caption": caption[:_MAX_CAPTION]},
            files={"audio": (audio.name, audio.read_bytes())},
        )

    # -- plumbing ---------------------------------------------------------

    async def _call(
        self,
        method: str,
        *,
        data: dict[str, object],
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> AlertResult:
        if not (self._token and self._chat):
            return AlertResult(
                channel=self.name, ok=False, detail="credentials not configured"
            )
        payload: dict[str, object] = {"chat_id": self._chat, **data}
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout_s) as client:
                response = await client.post(
                    _API.format(token=self._token, method=method),
                    data=payload,
                    files=files,
                )
        except httpx.HTTPError as e:
            # Network down is expected, not exceptional: the station runs on
            # wifi. Log it, record the failure, let the pipeline continue.
            logger.warning("alert.telegram_failed", method=method, detail=str(e))
            return AlertResult(channel=self.name, ok=False, detail=str(e))
        if response.status_code == 200:
            return AlertResult(channel=self.name, ok=True, detail=response.text[:200])
        detail = f"HTTP {response.status_code}: {response.text[:200]}"
        logger.warning("alert.telegram_failed", method=method, detail=detail)
        return AlertResult(channel=self.name, ok=False, detail=detail)

    async def healthcheck(self) -> bool:
        return bool(self._token and self._chat)
