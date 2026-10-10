"""Core dataclasses shared by every pipeline stage.

Everything else in the codebase is defined against these shapes
(docs/architecture.md §4). Import from here; never redefine a shape locally.

Data only — behavior lives behind Protocols in the modules that implement it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import numpy as np
import numpy.typing as npt
from ulid import ULID


def new_id() -> str:
    """Mint a ULID string.

    ULIDs, not UUIDs, because they sort chronologically — and this system's
    entire data model is ordered by time (docs/conventions.md §3).
    """
    return str(ULID())


class Severity(StrEnum):
    CRITICAL = "critical"  # explicit distress, LLM confirmed
    URGENT = "urgent"  # pan-pan, distress semantics, USCG relay
    WATCH = "watch"  # ambiguous, low confidence
    ROUTINE = "routine"  # logged only


@dataclass
class AudioFrame:
    """One block of capture, as handed out by an AudioSource."""

    pcm: npt.NDArray[np.float32]  # float32 mono, -1.0..1.0
    sample_rate: int
    captured_at: datetime  # UTC, timezone-aware
    source_name: str


@dataclass
class Transmission:
    """One segmented radio transmission, pre-roll included.

    The signal stats (rms/peak/SNR) are stored on every segment because they
    drive hallucination gating and are the first diagnostic you reach for
    when tuning thresholds (docs/architecture.md §5.2).
    """

    id: str  # ulid
    channel: str  # "16"
    started_at: datetime  # UTC, timezone-aware
    ended_at: datetime  # UTC, timezone-aware
    duration_ms: int
    pcm: npt.NDArray[np.float32]  # float32 mono @ 16 kHz, includes pre-roll
    sample_rate: int
    rms_dbfs: float
    peak_dbfs: float
    est_snr_db: float | None
    audio_path: str | None  # set once archived
    audio_sha256: str | None
    source: str  # "live" | "file:<name>"


@dataclass
class Word:
    text: str
    start_s: float
    end_s: float
    confidence: float | None


@dataclass
class Transcript:
    """One ASR pass over one transmission.

    Kept separate from Transmission (own table, own rows) because the archive
    will be re-transcribed with better models later and the results need to
    be comparable (docs/architecture.md §5.8).
    """

    transmission_id: str
    engine: str  # e.g. "mlx-whisper:small.en"
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    language: str | None = None
    latency_ms: int | None = None


@dataclass
class Detection:
    id: str
    transmission_id: str
    severity: Severity
    confidence: float  # 0..1
    matched_terms: list[str]
    tier: str  # "watchword" | "llm" | "both"
    classifier_output: dict[str, object] | None
    created_at: datetime  # UTC, timezone-aware


@dataclass
class IncidentSummary:
    """LLM-extracted facts about an incident.

    Every field except the quote is nullable on purpose: the classifier is
    instructed to report only what was stated and never infer — a fabricated
    latitude is far worse than a null one (docs/architecture.md §5.5).
    """

    nature_of_emergency: str | None
    vessel_name: str | None
    vessel_description: str | None
    position_as_stated: str | None
    latitude: float | None
    longitude: float | None
    persons_aboard: int | None
    injuries_reported: str | None
    verbatim_quote: str
    reasoning: str


@dataclass
class AlertResult:
    """Outcome of one send attempt on one channel.

    Kept as data (not an exception) because a failed channel is routine —
    the router logs it and the other channels still fire. `detail` carries
    the error message or the provider's receipt for the delivery log.
    """

    channel: str
    ok: bool
    detail: str | None = None
    # True only when trying again could plausibly work: the network was down,
    # the provider rate-limited us, or it had a 5xx. A rejected token or a
    # malformed request will fail identically forever, and retrying it just
    # hides a configuration error behind a delay.
    retryable: bool = False


@dataclass
class Incident:
    """One real-world emergency, spanning many transmissions.

    The top-level object (docs/decisions.md D12): alerts fire per incident,
    not per transmission, so one mayday means one notification plus updates.
    """

    id: str
    opened_at: datetime  # UTC, timezone-aware
    last_activity_at: datetime
    closed_at: datetime | None
    status: str  # "open" | "closed"
    severity: Severity
    channels: list[str]
    detection_ids: list[str]
    summary: IncidentSummary | None
    confidence: float
    alerted_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
