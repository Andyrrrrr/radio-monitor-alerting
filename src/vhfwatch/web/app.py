"""The incident-record web app: FastAPI, server-rendered, separate process.

Runs apart from the pipeline on purpose — a hung request handler must never
stop detection (docs/decisions.md D8). It reads the store and writes only
acknowledgments, feedback, and annotations.

Access model (POC): requests from loopback are trusted (the default bind is
127.0.0.1, so remote exposure is a deliberate config change), and so are
requests from `[web] trusted_networks` (the station LAN and Tailscale,
docs/decisions.md D36); anyone else needs a per-recipient share token
minted into the alert link. All access to incident records is logged.

The Marine 1 page (/marine1) puts the channel 16 feed beside NOAA tides,
the NWS marine forecast and webcam links (web/conditions.py).

Everything rendered is evidence-first: original audio and raw transcript
are always present, the LLM summary sits beside them (never instead), and
uncertainty is drawn honestly — low-confidence words greyed, per-
transmission signal glyph, which tier fired in plain language.
"""

import asyncio
import ipaddress
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from vhfwatch.audio.encode import playback_path_for
from vhfwatch.config import Settings
from vhfwatch.detect.hallucination import gate_transcript
from vhfwatch.detect.position import OUTSIDE_AREA_NOTE, find_positions, position_parts
from vhfwatch.models import Transcript
from vhfwatch.store import Database
from vhfwatch.web.conditions import Conditions, next_tide, tide_curve
from vhfwatch.web.tokens import verify_token

logger = structlog.get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Presentation thresholds for honest uncertainty rendering. Display-only —
# they change how words look, never what is stored or matched.
_LOW_CONFIDENCE = 0.5
_SNR_BARS = [(20.0, "▂▄▆█"), (12.0, "▂▄▆"), (6.0, "▂▄"), (float("-inf"), "▂")]

_LIVE_POLL_S = 2.0


def _snr_glyph(est_snr_db: float | None) -> str:
    if est_snr_db is None:
        return "?"
    for threshold, glyph in _SNR_BARS:
        if est_snr_db >= threshold:
            return glyph
    return "▂"


def _tier_words(tier: str) -> str:
    return {
        "watchword": "keyword match only (offline tier)",
        "llm": "language-model classification only — no keyword matched",
        "both": "keyword match, confirmed by language-model classification",
        "window": "Coast Guard mayday/pan-pan earlier, place named in a later transmission",
    }.get(tier, tier)


def _age(then: datetime | None, now: datetime) -> str:
    """'just now', '7 min ago', '3 h ago': how old a fetched tile is."""
    if then is None:
        return "never"
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 120:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def create_app(cfg: Settings, conditions: Conditions | None = None) -> FastAPI:
    app = FastAPI(title="vhf-watch", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    db = Database(cfg.general.data_dir / "vhfwatch.db", allow_threads=True)
    db.migrate()
    trusted = [ipaddress.ip_network(n, strict=False) for n in cfg.web.trusted_networks]
    tz = ZoneInfo(cfg.general.local_timezone)
    dash = cfg.web.dashboard
    conditions = conditions or Conditions(dash, cfg.general.local_timezone)

    def is_trusted(host: str) -> bool:
        """Loopback, or a source address inside `[web] trusted_networks`."""
        if host in ("127.0.0.1", "::1", "localhost"):
            return True
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(addr in net for net in trusted)

    def authorize(request: Request, incident_id: str | None) -> None:
        """Loopback and trusted networks are trusted; anything else needs a
        valid share token (scoped to the incident when one is in play).
        Always logged."""
        host = request.client.host if request.client else ""
        raw = request.query_params.get("token")
        if is_trusted(host):
            db.log_access(None, str(request.url.path), host)
            return
        if raw:
            granted = verify_token(db, raw)
            if granted is not None and (incident_id is None or granted == incident_id):
                db.log_access(None, str(request.url.path), host)
                return
        logger.warning("web.access_denied", path=str(request.url.path), host=host)
        raise HTTPException(status_code=403, detail="valid share token required")

    # -- read routes -----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        authorize(request, None)
        incidents = [dict(r) for r in db.list_incidents(limit=100)]
        for inc in incidents:
            inc["summary"] = json.loads(inc["summary"]) if inc["summary"] else None
            inc["channels"] = json.loads(inc["channels"])
        return templates.TemplateResponse(
            request, "index.html", {"incidents": incidents}
        )

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    def incident_page(request: Request, incident_id: str) -> HTMLResponse:
        authorize(request, incident_id)
        row = db.get_incident(incident_id)
        if row is None:
            raise HTTPException(status_code=404)
        incident = dict(row)
        incident["summary"] = (
            json.loads(incident["summary"]) if incident["summary"] else None
        )
        incident["channels"] = json.loads(incident["channels"])

        timeline = _build_timeline(db, incident_id)
        token = request.query_params.get("token", "")
        return templates.TemplateResponse(
            request,
            "incident.html",
            {
                "incident": incident,
                "timeline": timeline,
                "annotations": [
                    dict(a) for a in db.list_annotations("incident", incident_id)
                ],
                "token": token,
            },
        )

    @app.get("/transmissions/{transmission_id}/audio")
    def transmission_audio(request: Request, transmission_id: str) -> FileResponse:
        authorize(request, None)
        row = db.get_transmission(transmission_id)
        if row is None or row["audio_path"] is None:
            raise HTTPException(status_code=404)
        archive = Path(row["audio_path"])
        # Serve the AAC playback copy when it exists — Opus-in-anything is
        # unreliable on iOS Safari and the crew is on iPhones (§5.8).
        playback = playback_path_for(archive)
        path = playback or archive
        if not path.exists():
            raise HTTPException(status_code=404)
        media_type = {
            ".m4a": "audio/mp4",
            ".opus": "audio/ogg",
            ".wav": "audio/wav",
        }.get(path.suffix, "application/octet-stream")
        return FileResponse(path, media_type=media_type)

    @app.get("/health")
    def health() -> JSONResponse:
        last_tx = db.last_transmission_at()
        open_incidents = len(db.list_open_incidents())
        return JSONResponse(
            {
                "last_transmission_at": last_tx,
                "open_incidents": open_incidents,
                "note": "pipeline-internal health (noise floor, queue depths, "
                "heartbeats) lands with Phase 4 — absence of data here is "
                "not evidence the pipeline is healthy",
            }
        )

    @app.get("/live")
    async def live(request: Request) -> StreamingResponse:
        authorize(request, None)

        async def stream() -> AsyncIterator[str]:
            cursor = ""
            rows = db.list_transmissions(limit=1)
            if rows:
                cursor = rows[0]["id"]
            while True:
                if await request.is_disconnected():
                    return
                for row in db.transmissions_after(cursor or "0"):
                    cursor = row["id"]
                    payload = json.dumps(
                        {
                            "id": row["id"],
                            "started_at": row["started_at"],
                            "duration_ms": row["duration_ms"],
                            "est_snr_db": row["est_snr_db"],
                        }
                    )
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(_LIVE_POLL_S)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -- Marine 1 page -----------------------------------------------------

    def token_query(request: Request) -> str:
        """Carry a share token through the page's own links and audio."""
        token = request.query_params.get("token", "")
        return f"?token={token}" if token else ""

    def feed_items(request: Request) -> list[dict[str, Any]]:
        tq = token_query(request)
        items: list[dict[str, Any]] = []
        for row in db.recent_feed(dash.feed_limit):
            started = datetime.fromisoformat(row["started_at"]).astimezone(tz)
            text = row["text"]
            # The same hallucination gate the pipeline runs before detection:
            # a rejected transcript is shown, but marked as likely noise.
            rejected = None
            if text is not None:
                rejected = gate_transcript(
                    Transcript(
                        transmission_id=row["id"],
                        engine="",
                        text=text,
                        avg_logprob=row["avg_logprob"],
                        no_speech_prob=row["no_speech_prob"],
                    ),
                    cfg.hallucination,
                    cfg.asr,
                )
            # Spoken coordinates as map lines, exactly as the Telegram caption
            # shows them, and likewise never read from a rejected transcript.
            positions = []
            if cfg.position.enabled and text and not rejected:
                for pos in find_positions(text):
                    label, outside, url = position_parts(
                        pos, cfg.position.expected_area
                    )
                    note = OUTSIDE_AREA_NOTE if outside else None
                    positions.append({"label": label, "note": note, "url": url})
            items.append(
                {
                    "id": row["id"],
                    "positions": positions,
                    # The Telegram caption's own line, so the two read alike.
                    "meta": (
                        f"ch {row['channel']} · {started:%H:%M:%S} · "
                        f"{row['duration_ms'] / 1000:.1f}s · SNR\u00a0"
                        + (
                            f"{row['est_snr_db']:.0f}\u00a0dB"
                            if row["est_snr_db"] is not None
                            else "?"
                        )
                    ),
                    "day": f"{started:%A, %B} {started.day}",
                    "clock": f"{started:%I:%M %p}".lstrip("0"),
                    "severity": row["severity"],
                    "text": text,
                    "rejected": rejected,
                    "audio_url": (
                        f"/transmissions/{row['id']}/audio{tq}"
                        if row["audio_path"]
                        else None
                    ),
                }
            )
        return items

    @app.get("/marine1", response_class=HTMLResponse)
    async def marine1(request: Request) -> HTMLResponse:
        authorize(request, None)
        stations = {s.id: s.name for s in dash.tide_stations}
        station = request.query_params.get("station") or dash.tide_stations[0].id
        if station not in stations:
            raise HTTPException(status_code=400, detail="unknown tide station")
        tides, forecast = await asyncio.gather(
            conditions.tides(station), conditions.forecast()
        )
        now = conditions.now()
        events = tides.value or []
        # The window shown: today and tomorrow, local time. The fetch reaches a
        # day either side so the curve has a high/low beyond each end.
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)
        tide_days: dict[str, list[dict[str, Any]]] = {}
        upcoming = next_tide(events, now)
        for event in (e for e in events if start <= e.at < end):
            tide_days.setdefault(f"{event.at:%A %b} {event.at.day}", []).append(
                {
                    "time": event.at.strftime("%H:%M"),
                    "kind": event.kind,
                    "height_ft": f"{event.height_ft:.1f}",
                    "is_next": event is upcoming,
                }
            )
        curve = tide_curve(events, start, end, timedelta(minutes=10))
        chart = (
            {
                # Epoch milliseconds, so the browser draws and labels them in
                # the station's timezone whatever the phone's own setting.
                "points": [[int(t.timestamp() * 1000), round(h, 2)] for t, h in curve],
                "events": [
                    [int(e.at.timestamp() * 1000), e.height_ft, e.kind]
                    for e in events
                    if start <= e.at < end
                ],
                "now": int(now.timestamp() * 1000),
                "tz": cfg.general.local_timezone,
            }
            if curve
            else None
        )
        return templates.TemplateResponse(
            request,
            "marine1.html",
            {
                "chart": chart,
                "feed": feed_items(request),
                "tq": token_query(request),
                "token": request.query_params.get("token", ""),
                "stations": stations,
                "station": station,
                "tides": tides,
                "tide_days": tide_days,
                "tides_age": _age(tides.fetched_at, now),
                "forecast": forecast,
                "forecast_age": _age(forecast.fetched_at, now),
                "zone": dash.forecast_zone,
                "webcams": dash.webcams,
            },
        )

    @app.get("/marine1/feed", response_class=HTMLResponse)
    def marine1_feed(request: Request) -> HTMLResponse:
        """The feed alone, for the page's live refresh."""
        authorize(request, None)
        return templates.TemplateResponse(
            request, "_feed.html", {"feed": feed_items(request)}
        )

    # -- write routes (the only ones) --------------------------------------

    @app.post("/incidents/{incident_id}/ack")
    def ack(
        request: Request, incident_id: str, by: str = Form(...), token: str = Form("")
    ) -> RedirectResponse:
        _authorize_form(request, incident_id, token)
        db.ack_incident(incident_id, by.strip() or "unknown")
        logger.info("incident.acknowledged", incident_id=incident_id, by=by)
        return _back_to_incident(incident_id, token)

    @app.post("/incidents/{incident_id}/feedback")
    def feedback(
        request: Request,
        incident_id: str,
        verdict: str = Form(...),
        by: str = Form("anonymous"),
        token: str = Form(""),
    ) -> RedirectResponse:
        _authorize_form(request, incident_id, token)
        if verdict not in ("useful", "not-useful"):
            raise HTTPException(
                status_code=400, detail="verdict must be useful|not-useful"
            )
        # The labeled-data engine (docs/roadmap.md Phase 3): every verdict
        # is an append-only annotation tying a human judgment to a record.
        db.insert_annotation("incident", incident_id, by, f"feedback: {verdict}")
        return _back_to_incident(incident_id, token)

    @app.post("/incidents/{incident_id}/annotations")
    def annotate(
        request: Request,
        incident_id: str,
        author: str = Form(...),
        body: str = Form(...),
        token: str = Form(""),
    ) -> RedirectResponse:
        _authorize_form(request, incident_id, token)
        if not body.strip():
            raise HTTPException(status_code=400, detail="empty annotation")
        db.insert_annotation("incident", incident_id, author.strip() or "unknown", body)
        return _back_to_incident(incident_id, token)

    def _authorize_form(request: Request, incident_id: str, token: str) -> None:
        host = request.client.host if request.client else ""
        if is_trusted(host):
            return
        if token and verify_token(db, token) == incident_id:
            return
        raise HTTPException(status_code=403, detail="valid share token required")

    def _back_to_incident(incident_id: str, token: str) -> RedirectResponse:
        suffix = f"?token={token}" if token else ""
        return RedirectResponse(
            url=f"/incidents/{incident_id}{suffix}", status_code=303
        )

    return app


def _build_timeline(db: Database, incident_id: str) -> list[dict[str, Any]]:
    """Chronological transmissions for an incident: audio + raw transcript
    + detection, with the display fields the template needs."""
    timeline: list[dict[str, Any]] = []
    for det in db.incident_detections(incident_id):
        tx = db.get_transmission(det["transmission_id"])
        if tx is None:
            continue
        transcripts = db.list_transcripts(det["transmission_id"])
        latest: sqlite3.Row | None = transcripts[-1] if transcripts else None
        words: list[dict[str, Any]] = []
        if latest is not None and latest["words"]:
            for w in json.loads(latest["words"]):
                words.append(
                    {
                        "text": w["text"],
                        "start_s": w["start_s"],
                        "low_confidence": (
                            w["confidence"] is not None
                            and w["confidence"] < _LOW_CONFIDENCE
                        ),
                    }
                )
        timeline.append(
            {
                "transmission": dict(tx),
                "text": latest["text"] if latest is not None else "(no transcript)",
                "engine": latest["engine"] if latest is not None else None,
                "words": words,
                "snr_glyph": _snr_glyph(tx["est_snr_db"]),
                "est_snr_db": tx["est_snr_db"],
                "matched_terms": json.loads(det["matched_terms"]),
                "tier": _tier_words(det["tier"]),
                "severity": det["severity"],
            }
        )
    timeline.sort(key=lambda t: t["transmission"]["started_at"])
    return timeline
