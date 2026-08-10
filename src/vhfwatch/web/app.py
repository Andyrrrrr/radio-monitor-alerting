"""The incident-record web app: FastAPI, server-rendered, separate process.

Runs apart from the pipeline on purpose — a hung request handler must never
stop detection (docs/decisions.md D8). It reads the store and writes only
acknowledgments, feedback, and annotations.

Access model (POC): requests from loopback are trusted (the default bind is
127.0.0.1, so remote exposure is a deliberate config change); remote
requests need a per-recipient share token minted into the alert link. All
access to incident records is logged.

Everything rendered is evidence-first: original audio and raw transcript
are always present, the LLM summary sits beside them (never instead), and
uncertainty is drawn honestly — low-confidence words greyed, per-
transmission signal glyph, which tier fired in plain language.
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from vhfwatch.store import Database
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
    }.get(tier, tier)


def create_app(cfg: Settings) -> FastAPI:
    app = FastAPI(title="vhf-watch", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    db = Database(cfg.general.data_dir / "vhfwatch.db", allow_threads=True)
    db.migrate()

    def authorize(request: Request, incident_id: str | None) -> None:
        """Loopback is trusted; anything else needs a valid share token
        (scoped to the incident when one is in play). Always logged."""
        host = request.client.host if request.client else ""
        raw = request.query_params.get("token")
        if host in ("127.0.0.1", "::1", "localhost"):
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
        if host in ("127.0.0.1", "::1", "localhost"):
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
