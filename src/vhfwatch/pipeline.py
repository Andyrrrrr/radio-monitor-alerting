"""Pipeline: audio → segmenter → ASR → detection → incidents → alerts.

Single asyncio process, bounded queues between stages (docs/decisions.md
D8). The stages:

    ingest     source frames → segmenter → archive + store → asr queue
    transcribe pre-ASR gate → whisper → post-ASR gate → store → detect queue
    detect     watchwords → LLM (optional) → detection → correlate → alert

Load-bearing properties, each with a home in the code below:
- **Ingest never blocks.** Queues are bounded (maxsize 32); on overflow the
  oldest *unflagged* item is dropped loudly. Nothing past the detect stage
  is ever dropped — flagged work is already an incident.
- **The alert path never depends on the LLM** (constraint 6): the detector
  degrades to Tier 1, and alert channels fail independently.
- **File and live sources are interchangeable** (constraint 4): everything
  below the source wrapper is identical, including the 16 kHz resample.
- **A stalled capture kills the process** (`CaptureStalled`). On 2026-09-23 the
  USB interface vanished, the process stayed alive and logged nothing for 50
  minutes, and a quiet channel is indistinguishable from a dead one. Recovery
  belongs to the supervisor (`systemd Restart=always`), not to in-process
  reconnect logic: one mechanism covers USB dropout, power cut and unplugged
  cable, and it cannot half-work.

Usage:
    python -m vhfwatch.pipeline --config config/config.toml
    python -m vhfwatch.pipeline --source file --path data/corpus/x.wav
    python -m vhfwatch.pipeline --source directory --path data/corpus
"""

import argparse
import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import structlog

from vhfwatch.alerting import AlertRouter, create_channels
from vhfwatch.asr import Transcriber, create_transcriber
from vhfwatch.audio.encode import archive_audio
from vhfwatch.audio.resample import ResamplingAudioSource
from vhfwatch.audio.segmenter import Segmenter
from vhfwatch.audio.sources import (
    AudioSource,
    DirectoryAudioSource,
    FileAudioSource,
    LiveAudioSource,
)
from vhfwatch.config import Settings, load_config
from vhfwatch.detect import (
    Classifier,
    Detector,
    WatchwordMatcher,
    gate_transcript,
    gate_transmission,
    unrunnable_checks,
)
from vhfwatch.incidents.correlator import DetectionContext, IncidentCorrelator
from vhfwatch.log import configure_logging
from vhfwatch.models import Incident, Severity, Transcript, Transmission
from vhfwatch.store import Database
from vhfwatch.web.tokens import mint_share_link

logger = structlog.get_logger(__name__)


class CaptureStalled(RuntimeError):
    """No audio frames arrived within `[health].capture_stall_s`.

    Raised so the process exits non-zero and the supervisor restarts it.
    Live sources only: a file source legitimately ends.
    """


# Backpressure bound per stage (docs/architecture.md §3). A genuine
# constant: Ch 16 is silent 97-99% of the time, so 32 in-flight segments
# already means transcription has fallen minutes behind.
_QUEUE_MAX = 32

# How often the live pipeline checks for quiet incidents to close. Coarse
# on purpose — the quiet period itself is minutes.
_TICK_INTERVAL_S = 15.0


@dataclass
class PipelineStats:
    transmissions: int = 0
    rejected_pre_asr: int = 0
    transcripts: int = 0
    rejected_post_asr: int = 0
    detections: int = 0
    incidents_opened: int = 0
    queue_drops: int = 0
    alerts_sent: int = 0
    # Wall-clock-independent: monotonic, so a clock step cannot fake liveness.
    last_frame_at: float = field(default_factory=time.monotonic)
    _open_incident_ids: set[str] = field(default_factory=set)


_T = TypeVar("_T")


def _drop_oldest_put(
    queue: "asyncio.Queue[_T | None]", item: _T, stage: str, stats: PipelineStats
) -> None:
    """Bounded put that never blocks ingest: drop the OLDEST queued item,
    loudly. Only pre-detection stages use this, so nothing dropped was
    flagged (docs/architecture.md §3)."""
    if queue.full():
        dropped = queue.get_nowait()
        stats.queue_drops += 1
        dropped_id = getattr(dropped, "id", None) or getattr(
            dropped, "transmission_id", None
        )
        logger.warning(
            "queue.overflow_drop",
            stage=stage,
            dropped_id=dropped_id,
            hint="transcription is falling behind ingest",
        )
    queue.put_nowait(item)


async def run(
    cfg: Settings,
    source: AudioSource,
    transcriber: Transcriber | None = None,
    router: AlertRouter | None = None,
) -> PipelineStats:
    """Run the full pipeline until the source is exhausted (file/directory)
    or cancelled (live). Returns counters for replay/evaluate output.

    `transcriber` and `router` are injectable for tests and scripts;
    by default both are built from config.
    """
    data_dir = cfg.general.data_dir
    audio_dir = data_dir / "audio"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Everything downstream runs at the target rate, whatever the source.
    if source.sample_rate != cfg.audio.target_rate:
        source = ResamplingAudioSource(source, cfg.audio.target_rate)

    if transcriber is None:
        transcriber = create_transcriber(cfg.asr)
    matcher = WatchwordMatcher.from_toml(cfg.detect)
    classifier = Classifier(cfg.detect)
    detector = Detector(cfg.detect, matcher, classifier)

    stats = PipelineStats()

    with Database(data_dir / "vhfwatch.db") as db:
        db.migrate()

        if router is None:
            channels = create_channels(
                cfg.alerting,
                cfg.general.local_timezone,
                link_for=lambda inc: mint_share_link(db, cfg.web, inc.id),
            )
            router = AlertRouter(cfg.alerting, channels)

        correlator = IncidentCorrelator(cfg.correlator)
        correlator.adopt(
            [_incident_from_row(row, db) for row in db.list_open_incidents()]
        )

        # First-inference model load is multi-second; pay it now, not on a
        # real mayday (AGENTS.md).
        await transcriber.warmup()

        asr_queue: asyncio.Queue[Transmission | None] = asyncio.Queue(_QUEUE_MAX)
        detect_queue: asyncio.Queue[Transcript | None] = asyncio.Queue(_QUEUE_MAX)

        segmenter = Segmenter(
            cfg.segmenter,
            sample_rate=cfg.audio.target_rate,
            channel=cfg.general.channel,
            source_name=source.name,
        )

        # Set when ingest finishes, so the watchdog stops watching a source
        # that ended on purpose rather than reporting a false stall.
        ingest_done = asyncio.Event()

        async def ingest() -> None:
            try:
                await _ingest_frames()
            finally:
                ingest_done.set()

        async def _ingest_frames() -> None:
            async for frame in source.frames():
                stats.last_frame_at = time.monotonic()
                for tx in segmenter.push(frame):
                    await _archive_and_store(db, tx, audio_dir, cfg)
                    stats.transmissions += 1
                    _drop_oldest_put(asr_queue, tx, "asr", stats)
            tail = segmenter.flush()
            if tail is not None:
                await _archive_and_store(db, tail, audio_dir, cfg)
                stats.transmissions += 1
                _drop_oldest_put(asr_queue, tail, "asr", stats)
            asr_queue.put_nowait(None)

        async def transcribe() -> None:
            while True:
                tx = await asr_queue.get()
                if tx is None:
                    detect_queue.put_nowait(None)
                    return
                reason = gate_transmission(tx, cfg.hallucination)
                if reason is not None:
                    stats.rejected_pre_asr += 1
                    logger.info(
                        "transcription.skipped",
                        transmission_id=tx.id,
                        reason=reason,
                    )
                    continue
                transcript = await transcriber.transcribe(tx)
                db.insert_transcript(transcript)
                stats.transcripts += 1
                missing = unrunnable_checks(transcript)
                if missing:
                    # Not a rejection — but the operator must be able to see
                    # that part of the hallucination gate did not run.
                    logger.warning(
                        "transcript.checks_unrunnable",
                        transmission_id=tx.id,
                        missing=",".join(missing),
                        engine=transcript.engine,
                    )
                reject = gate_transcript(transcript, cfg.hallucination, cfg.asr)
                if reject is not None:
                    # Stored (it's evidence for corpus review) but never
                    # forwarded to detection — hallucination gating runs
                    # BEFORE watchword matching, always.
                    stats.rejected_post_asr += 1
                    logger.info(
                        "transcript.rejected",
                        transmission_id=tx.id,
                        reason=reject,
                        text=transcript.text[:120],
                    )
                    continue
                _drop_oldest_put(detect_queue, transcript, "detect", stats)

        async def detect() -> None:
            # The classifier's conversation context: recent accepted texts.
            context: deque[str] = deque(maxlen=cfg.detect.llm_context_count)
            while True:
                transcript = await detect_queue.get()
                if transcript is None:
                    return
                detection = await detector.detect(transcript, list(context))
                context.append(transcript.text)
                if detection is None:
                    continue
                db.insert_detection(detection)
                stats.detections += 1

                summary = None
                if detection.classifier_output is not None:
                    from vhfwatch.detect.classify import LLMAssessment

                    summary = LLMAssessment(**detection.classifier_output).to_summary()
                result = correlator.add(
                    detection,
                    DetectionContext(
                        channel=cfg.general.channel,
                        transcript_text=transcript.text,
                        summary=summary,
                    ),
                )
                if result.is_new:
                    stats.incidents_opened += 1
                    stats._open_incident_ids.add(result.incident.id)
                    db.insert_incident(result.incident)
                else:
                    db.update_incident(result.incident)

                alert_results = await router.dispatch(
                    result.incident, result.is_new, result.escalated
                )
                for ar in alert_results:
                    db.insert_alert(
                        result.incident.id,
                        ar.channel,
                        is_update=not result.is_new,
                        ok=ar.ok,
                        detail=ar.detail,
                    )
                    if ar.ok:
                        stats.alerts_sent += 1
                if alert_results:
                    db.update_incident(result.incident)  # persist alerted_at

                for closed in correlator.tick(detection.created_at):
                    db.update_incident(closed)

        async def tick_quiet_incidents() -> None:
            # Wall-clock tick for live operation: with no new detections,
            # quiet incidents must still close. Cancelled at shutdown.
            from datetime import UTC, datetime

            drift_warned = False
            while True:
                await asyncio.sleep(_TICK_INTERVAL_S)
                for closed in correlator.tick(datetime.now(UTC)):
                    db.update_incident(closed)
                # Noise-floor drift vs the calibrated value: a drop means
                # the audio chain broke, a rise means a level or
                # interference change — either way the calibrated
                # thresholds no longer hold (docs/architecture.md §5.9).
                measured = segmenter.noise_floor_db
                calibrated = cfg.audio.calibration.noise_floor_dbfs
                if (
                    not drift_warned
                    and measured is not None
                    and abs(measured - calibrated) > cfg.audio.calibration.drift_warn_db
                ):
                    drift_warned = True
                    logger.warning(
                        "audio.noise_floor_drift",
                        measured_dbfs=round(measured, 1),
                        calibrated_dbfs=calibrated,
                        hint="re-run scripts/calibrate.py or find what "
                        "changed in the audio chain",
                    )

        async def capture_watchdog() -> None:
            """Assert frames are still ARRIVING, not merely that we are alive.

            The noise-floor drift warning is not a substitute: it fires only
            when the estimate MOVES, and a dead stream moves nothing.
            """
            stall_s = float(cfg.health.capture_stall_s)
            interval = min(stall_s / 2.0, _TICK_INTERVAL_S)
            while True:
                try:
                    await asyncio.wait_for(ingest_done.wait(), timeout=interval)
                    return  # the source ended deliberately; nothing to watch
                except TimeoutError:
                    pass
                silent_for = time.monotonic() - stats.last_frame_at
                if silent_for > stall_s:
                    logger.error(
                        "audio.capture_stalled",
                        silent_for_s=round(silent_for, 1),
                        capture_stall_s=stall_s,
                        hint="no frames from the audio source — exiting so the "
                        "supervisor restarts us (docs/hardware.md §3.9)",
                    )
                    raise CaptureStalled(
                        f"no audio frames for {silent_for:.0f}s (limit {stall_s:.0f}s)"
                    )

        ticker = asyncio.create_task(tick_quiet_incidents())
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(ingest())
                if source.name == "live":
                    tg.create_task(capture_watchdog())
                tg.create_task(transcribe())
                tg.create_task(detect())
        finally:
            ticker.cancel()
            await source.close()

    logger.info(
        "pipeline.finished",
        source=source.name,
        transmissions=stats.transmissions,
        transcripts=stats.transcripts,
        rejected_pre_asr=stats.rejected_pre_asr,
        rejected_post_asr=stats.rejected_post_asr,
        detections=stats.detections,
        incidents_opened=stats.incidents_opened,
        queue_drops=stats.queue_drops,
        discarded_segments=segmenter.discarded,
        noise_floor_db=(
            round(segmenter.noise_floor_db, 1)
            if segmenter.noise_floor_db is not None
            else None
        ),
    )
    return stats


async def _archive_and_store(
    db: Database, tx: Transmission, audio_dir: Path, cfg: Settings
) -> None:
    # Archive before insert: the row arrives complete, and the append-only
    # trigger never needs to allow a post-hoc audio_path update. Encoding
    # runs in a thread (ffmpeg blocks); the sqlite write stays on the loop
    # because the connection is thread-bound.
    path, sha256 = await asyncio.to_thread(archive_audio, tx, audio_dir, cfg.storage)
    tx.audio_path = str(path)
    tx.audio_sha256 = sha256
    db.insert_transmission(tx)
    logger.info(
        "transmission.stored",
        transmission_id=tx.id,
        duration_ms=tx.duration_ms,
        audio_path=str(path),
    )


def _incident_from_row(row: object, db: Database) -> Incident:
    """Rehydrate an open incident row for correlator adoption."""
    import json
    from datetime import datetime

    from vhfwatch.models import IncidentSummary

    summary = None
    if row["summary"]:  # type: ignore[index]
        summary = IncidentSummary(**json.loads(row["summary"]))  # type: ignore[index]
    detection_ids = [d["id"] for d in db.incident_detections(row["id"])]  # type: ignore[index]
    return Incident(
        id=row["id"],  # type: ignore[index]
        opened_at=datetime.fromisoformat(row["opened_at"]),  # type: ignore[index]
        last_activity_at=datetime.fromisoformat(row["last_activity_at"]),  # type: ignore[index]
        closed_at=None,
        status="open",
        severity=Severity(row["severity"]),  # type: ignore[index]
        channels=json.loads(row["channels"]),  # type: ignore[index]
        detection_ids=detection_ids,
        summary=summary,
        confidence=row["confidence"],  # type: ignore[index]
        alerted_at=(
            datetime.fromisoformat(row["alerted_at"])  # type: ignore[index]
            if row["alerted_at"]  # type: ignore[index]
            else None
        ),
        acknowledged_at=(
            datetime.fromisoformat(row["acknowledged_at"])  # type: ignore[index]
            if row["acknowledged_at"]  # type: ignore[index]
            else None
        ),
        acknowledged_by=row["acknowledged_by"],  # type: ignore[index]
    )


def build_source(cfg: Settings, kind: str, path: Path | None) -> AudioSource:
    if kind == "file":
        if path is None:
            raise SystemExit("--source file requires --path <file.wav>")
        return FileAudioSource(
            path, blocksize=cfg.audio.blocksize, channel=cfg.audio.use_channel
        )
    if kind == "directory":
        if path is None:
            raise SystemExit("--source directory requires --path <dir>")
        return DirectoryAudioSource(
            path, blocksize=cfg.audio.blocksize, channel=cfg.audio.use_channel
        )
    if kind == "live":
        return LiveAudioSource(cfg.audio)
    raise SystemExit(f"unknown source kind: {kind}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vhfwatch.pipeline",
        description="Monitor a VHF audio stream for distress traffic.",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    parser.add_argument(
        "--source",
        choices=["file", "live", "directory"],
        default=None,
        help="audio source kind (default: [audio].source from config)",
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="WAV file or corpus directory"
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    cfg = load_config(args.config)
    kind = args.source or cfg.audio.source
    source = build_source(cfg, kind, args.path)
    try:
        asyncio.run(run(cfg, source))
    except KeyboardInterrupt:
        logger.info("pipeline.stopped", reason="keyboard interrupt")
    except BaseExceptionGroup as eg:
        # TaskGroup wraps; subgroup() also handles nesting. Anything that
        # isn't a stall propagates untouched — this is not a catch-all.
        if eg.subgroup(CaptureStalled) is None:
            raise
        # Non-zero exit is the point: systemd Restart=always brings us back
        # with a fresh audio stream. Do not try to recover in-process.
        logger.error("pipeline.stopped", reason="capture stalled")
        raise SystemExit("audio capture stalled; exiting for restart") from None


if __name__ == "__main__":
    main()
