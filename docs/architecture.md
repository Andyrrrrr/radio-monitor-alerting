# Architecture

Module layout, interfaces, data model, and the reasoning behind the decisions that aren't obvious.

---

## 1. Design constraints

These drive everything below. When a decision looks arbitrary, check here first.

1. **Voice-only.** DSC and AIS are better signals but the target vessels don't carry the equipment. Voice on Ch 16 is load-bearing. DSC/AIS may be added as supplements; nothing may depend on them.
2. **No RSSI.** A physical radio exposes audio, not signal strength. Segmentation is audio-energy VAD, not signal-strength gating.
3. **Develop on Apple Silicon, deploy on x86 Linux.** Every platform-specific dependency sits behind an interface with at least two implementations.
3a. **Two development rigs with different audio interfaces** (MOTU M2, Behringer UFO202). Device identity, gain, and thresholds are per-machine config, never hardcoded. Calibration values do not transfer between rigs.
4. **Local-first.** The listening and alerting path must work with the network unplugged. Cloud services are optional upgrades, never requirements.
5. **Files and live audio are the same thing.** The pipeline must run against recorded WAVs identically to live input. This enables offline development, deterministic tests, and tuning against a corpus.
6. **The alert path never depends on the summarization path.** If the LLM is down, the alert still goes out with the raw transcript and the matched watchword.
7. **Fail loudly.** Silent failure is the worst outcome. Every stage heartbeats; extended silence is itself an alertable condition.
8. **Append-only records.** Corrections are annotations. Nothing overwrites a transcript or a summary.

---

## 2. Repository layout

```
vhf-watch/
├── AGENTS.md                    # working context for coding agents
├── CLAUDE.md                    # one line: @AGENTS.md
├── README.md
├── pyproject.toml
├── config/
│   ├── config.example.toml
│   └── watchwords.example.toml
├── data/                        # gitignored — audio, db, corpus
│   ├── audio/
│   ├── corpus/
│   └── vhfwatch.db
├── docs/
│   ├── architecture.md          # this file
│   ├── roadmap.md
│   ├── decisions.md
│   ├── conventions.md
│   ├── definition-of-done.md
│   ├── hardware.md
│   ├── bringup-log.md
│   ├── status.json
│   └── plans/
├── src/vhfwatch/
│   ├── config.py                # pydantic-settings, TOML loading
│   ├── models.py                # shared dataclasses (see §4)
│   ├── pipeline.py              # asyncio wiring of all stages
│   ├── audio/
│   │   ├── sources.py           # AudioSource: Live, File, Directory
│   │   ├── segmenter.py         # VAD + ring buffer + hysteresis
│   │   └── encode.py            # opus archive + aac playback copies
│   ├── asr/
│   │   ├── base.py              # Transcriber protocol
│   │   ├── mlx.py               # Apple Silicon (dev default)
│   │   ├── faster_whisper.py    # portable / x86 default
│   │   └── deepgram.py          # optional cloud
│   ├── detect/
│   │   ├── watchwords.py        # tier 1: fuzzy + phonetic
│   │   ├── classify.py          # tier 2: LLM
│   │   └── hallucination.py     # ASR artifact filtering
│   ├── incidents/
│   │   └── correlator.py        # detections → incidents
│   ├── alerting/
│   │   ├── base.py              # AlertChannel protocol
│   │   ├── console.py           # dev
│   │   ├── macos.py             # dev, osascript notifications
│   │   ├── pushover.py
│   │   ├── telegram.py          # recording + transcript in one message (D26)
│   │   ├── outbox.py            # non-blocking delivery, retry, gap report (D28)
│   │   └── router.py            # severity → channels, escalation
│   ├── store/
│   │   ├── schema.sql
│   │   └── db.py
│   ├── health/
│   │   └── watchdog.py          # stall exceptions, StageTracker, daily check (D24, D28)
│   └── web/
│       ├── app.py               # FastAPI
│       └── templates/
├── scripts/
│   ├── audio_devices.py         # list Core Audio / ALSA inputs
│   ├── calibrate.py             # measure noise floor, suggest threshold
│   ├── record_corpus.py         # continuous timestamped recording
│   ├── replay.py                # push a WAV through the live pipeline
│   └── evaluate.py              # precision/recall against labels
└── tests/
    ├── fixtures/                # short synthetic WAVs, committed
    └── ...
```

**`data/` is gitignored and must stay that way.** It will contain recordings of real radio traffic involving identifiable people and vessels. Committing that to a public GitHub repo is the single worst mistake available in this project. Test fixtures in `tests/fixtures/` must be synthetic or self-recorded on an authorized channel.

---

## 3. Pipeline

```
AudioSource ──frames──> Segmenter ──Transmission──> Transcriber
                                                        │
                                                    Transcript
                                                        │
                                                        ▼
                                                    Detector
                                              (watchwords → LLM)
                                                        │
                                                    Detection
                                                        │
                                                        ▼
                                              IncidentCorrelator
                                                        │
                                          Incident (opened / updated)
                                                        │
                                                        ▼
                                                  AlertRouter
                                                        │
                        ┌───────────────┬───────────────┴────────┐
                        ▼               ▼                        ▼
                    console/macOS   Pushover              (local buzzer,
                      (dev)                                later on GPIO)

  Store (SQLite + audio files) ← written by every stage
  Web app (separate process) ← reads Store only
  Watchdog ← heartbeats from every stage
```

**Single process, `asyncio.Queue` between stages.** Channel 16 is silent 97–99% of the time; throughput is trivial. One process means one log stream and one restart policy. Do not reach for Docker, Celery, or microservices.

**Exception: the web app is a separate process.** A hung request handler must not be able to stop detection. It reads the database and never blocks the pipeline.

**Backpressure:** bounded queues (`maxsize=32`). If transcription falls behind, drop the *oldest* non-flagged segments and log it loudly — never block ingest, and never drop something already flagged as a possible detection.

---

## 4. Core types

Put these in `src/vhfwatch/models.py`. Everything else is defined against them.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import numpy as np

class Severity(str, Enum):
    CRITICAL = "critical"   # explicit distress, LLM confirmed
    URGENT   = "urgent"     # pan-pan, distress semantics, USCG relay
    WATCH    = "watch"      # ambiguous, low confidence
    ROUTINE  = "routine"    # logged only

@dataclass
class AudioFrame:
    pcm: np.ndarray          # float32 mono, -1.0..1.0
    sample_rate: int
    captured_at: datetime
    source_name: str

@dataclass
class Transmission:
    id: str                  # ulid
    channel: str             # "16"
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    pcm: np.ndarray          # float32 mono @ 16 kHz, includes pre-roll
    sample_rate: int
    rms_dbfs: float
    peak_dbfs: float
    est_snr_db: float | None
    audio_path: str | None   # set once archived
    audio_sha256: str | None
    source: str              # "live" | "file:<name>"

@dataclass
class Word:
    text: str
    start_s: float
    end_s: float
    confidence: float | None

@dataclass
class Transcript:
    transmission_id: str
    engine: str              # "mlx-whisper:small.en"
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
    confidence: float                    # 0..1
    matched_terms: list[str]
    tier: str                            # "watchword" | "llm" | "both"
    classifier_output: dict | None
    created_at: datetime

@dataclass
class IncidentSummary:
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
class Incident:
    id: str
    opened_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None
    status: str                          # "open" | "closed"
    severity: Severity
    channels: list[str]
    detection_ids: list[str]
    summary: IncidentSummary | None
    confidence: float
    alerted_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
```

---

## 5. Interfaces

### 5.1 Audio sources

The abstraction that makes offline development and deterministic testing possible. **Build `FileAudioSource` first.**

```python
from typing import Protocol, AsyncIterator

class AudioSource(Protocol):
    name: str
    sample_rate: int
    async def frames(self) -> AsyncIterator[AudioFrame]: ...
    async def close(self) -> None: ...
```

Implementations:

- **`FileAudioSource`** — reads a WAV, yields frames. Optional `realtime: bool`; when `False`, runs as fast as possible for tests, when `True`, paces to wall clock for realistic replay.
- **`DirectoryAudioSource`** — plays a corpus directory in timestamp order. The tuning workhorse.
- **`LiveAudioSource`** — `sounddevice` input stream. Config: device name or index, sample rate, block size. Must survive device disconnect and reconnect without crashing the process.

Capture at the device's native rate (usually 48 kHz), resample to **16 kHz mono float32** for everything downstream. Whisper wants 16 kHz; marine VHF audio is ~3 kHz bandwidth, so nothing is lost. Implemented as `ResamplingAudioSource` (`audio/resample.py`, streaming `soxr`) wrapping any source, so the pipeline sees 16 kHz frames whether the input is a 48 kHz live stream or an already-16 kHz fixture.

**Both supported interfaces present 2 input channels.** Slice `use_channel`; never average, or you lose 6 dB when the other channel is silent and invalidate the VAD calibration. The correct value is per-rig and set by which physical jack the radio is in — see `docs/hardware.md` §3.7.

**`use_channel` describes the capture device, not files.** `record_corpus.py` writes **mono** WAVs with the slice already applied, so `FileAudioSource` ignores `channel` for mono input (logging `file.mono_ignoring_channel`). Without that, a rig configured with `use_channel = 1` could not replay the corpus it recorded itself. An out-of-range channel on a genuinely multi-channel file still fails loudly.

**Apply `input_gain_db` after capture, before segmentation.** This exists for rigs with no hardware input trim (Behringer UFO202). It can raise a too-quiet signal; it cannot undo clipping. Log a warning at startup if `input_gain_db` exceeds ~12 dB — that indicates an analog problem that should be fixed upstream.

### 5.2 Segmenter

The core engineering problem. Concrete starting parameters — put them all in config, none hardcoded:

```toml
[segmenter]
sample_rate         = 16000
frame_ms            = 20
open_threshold_db   = -42    # derive from measured noise floor + 10 dB
close_threshold_db  = -48    # hysteresis: close lower than open
open_frames         = 2      # 40 ms above threshold to open
hang_ms             = 800    # below threshold before closing
preroll_ms          = 300    # ring buffer prepended on open
min_duration_ms     = 600    # discard shorter
max_duration_ms     = 120000 # force close a stuck transmitter
```

Requirements:

- **Ring buffer for pre-roll is mandatory.** Squelch clips the start of transmissions; without pre-roll you lose the first syllable, and "ayday" is a missed detection.
- **Hysteresis and hang time are what stop one transmission becoming five fragments.** Natural pauses in "Mayday, mayday, mayday — this is vessel Serenity" must not split.
- **Compute and store `rms_dbfs`, `peak_dbfs`, and estimated SNR** for every segment. These drive hallucination gating and are the diagnostic you'll want most.
- **Flag clipping** (`peak_dbfs > -0.5`) and warn. Clipped audio transcribes badly and the fix is a knob, not code.
- Start with an RMS gate. Add Silero VAD as an alternative implementation behind the same interface if you move to open squelch.

### 5.3 Transcriber

```python
class Transcriber(Protocol):
    name: str
    async def transcribe(self, tx: Transmission) -> Transcript: ...
    async def warmup(self) -> None: ...
```

| Implementation | Platform | Role |
|---|---|---|
| `MLXWhisperTranscriber` | Apple Silicon | **Dev default.** `mlx-whisper` is materially faster than alternatives on M-series. |
| `FasterWhisperTranscriber` | Portable, x86 + ARM | **Deployment default.** CTranslate2; CPU-only on Mac (no Metal) but runs everywhere. |
| `DeepgramTranscriber` | Cloud | Optional upgrade, **deliberately not built for the POC** (local-first); selecting it in config fails loudly. |

Select by config, not by platform detection at import time. **`warmup()` must be called at startup** — first-inference model load is multi-second and you don't want that latency on a real mayday.

Whisper configuration that matters:

```python
condition_on_previous_text = False   # stops hallucination loops feeding themselves
no_speech_threshold        = 0.6
logprob_threshold          = -1.0
temperature                = 0.0     # determinism for tuning
initial_prompt = (
    "Marine VHF radio traffic on channel 16. Mayday, pan-pan, securite, "
    "Coast Guard, vessel, motor vessel, sailing vessel, position, latitude, "
    "longitude, persons on board, taking on water, man overboard, "
    "abandoning ship, over, out, standing by, radio check."
)
```

Start with `small.en`. Compare `base.en`, `small.en`, and `medium.en` on the corpus and pick on measured WER, not vibes — a real accuracy/latency curve on *your* audio is a Phase 1 deliverable.

### 5.4 Hallucination filtering

Whisper confidently invents text on noise-only and near-silent audio. **This will fire watchwords if unguarded.** Filter before the detector:

1. **Pre-ASR gate:** skip transmissions below `min_duration_ms` or with SNR below threshold. Don't transcribe what can't be speech.
2. **Post-ASR gate:** discard when `no_speech_prob` is high or `avg_logprob` is poor.
3. **Blocklist** of known artifacts, case-insensitive, substring match:
   - "thank you for watching", "please subscribe", "thanks for watching"
   - "subtitles by", "amara.org", "transcription by"
   - "[music]", "[applause]", "[inaudible]"
   - Any single token repeated more than 3 times consecutively
4. **Log every rejection with its reason.** You need to know what you're throwing away, and a rejected segment that turns out to be a real mayday is exactly what the corpus review should surface.

Post-ASR rejects are still **stored** in the transcript table (they're evidence for corpus review) — they just never reach the detector.

### 5.5 Detector

Two tiers, in order.

**Tier 1 — watchwords (fast, offline, always runs).**

Literal matching misses real distress calls: "mayday" degrades to "may day", "made a", "mated", "hey day". So:

1. Normalize: lowercase, strip punctuation, collapse whitespace.
2. Exact and substring match on the configured term list (word-boundary
   padded — "aground" must not fire inside "background").
3. **Phonetic match** via classic Metaphone (`jellyfish` — it does not ship
   Double Metaphone; see `docs/decisions.md` D14) with guards: codes may
   differ by one edit ("mated" MTT vs "mayday" MT), the matched window must
   also pass a Jaro-Winkler similarity floor (`phonetic_min_similarity`),
   and terms with tokens shorter than `phonetic_min_token_len` skip this
   pass entirely ("cpr" vs "copy").
4. **Bounded fuzzy match** via `rapidfuzz`, ratio ≥ 0.82, on multi-word
   phrases, against token windows of the same length.

**Tier 2 — LLM classification.**

Runs when Tier 1 partially matched, **or** when the transcript exceeds a word-count threshold regardless of match. That second condition matters: *"we're taking on water fast, three people aboard"* contains no watchword and is exactly what you want to catch.

Requirements:

- Structured output against the `IncidentSummary` schema.
- Explicit instruction: **report only what is stated; use null for anything not stated; never infer a position, vessel name, or casualty count.** A fabricated latitude is far worse than a null one.
- Pass the preceding 2–3 transmissions as context. Distress traffic is a conversation and a single 4-second clip is often uninterpretable alone.
- **Must degrade gracefully.** On timeout or error, emit the Tier 1 result with its severity and note that classification was unavailable. Never block the alert.
- Timeout ~5 s. Use a fast model — this is in the critical path.

**Severity assignment:**

| Severity | Condition |
|---|---|
| CRITICAL | "mayday" matched (any tier) AND LLM confirms distress |
| URGENT | pan-pan, USCG mayday relay, or distress semantics without the keyword |
| WATCH | partial/fuzzy match only, or LLM confidence below threshold |
| ROUTINE | everything else |

Watchwords live in `config/watchwords.toml`, fully user-editable, grouped by severity. Starting set:

```toml
[critical]
terms = ["mayday", "may day", "mayday relay", "abandoning ship",
         "vessel sinking", "we are sinking"]

[urgent]
terms = ["pan pan", "pan-pan", "man overboard", "person in the water",
         "taking on water", "vessel on fire", "smoke aboard", "flooding",
         "capsized", "collision", "medical emergency", "not breathing",
         "unconscious", "cpr", "coast guard coast guard coast guard"]

[watch]
terms = ["require assistance", "need help", "aground", "dead in the water",
         "engine failure", "adrift", "securite", "out of fuel"]
```

Note: a USCG **mayday relay** broadcast is high-value signal, not a false positive. The Coast Guard rebroadcasting a distress call is precisely the alert you want.

### 5.6 Incident correlator

One emergency produces many transmissions. The crew wants one notification, not fifteen.

Correlation signals, in order of reliability:

1. **Fuzzy vessel-name match** across transcripts within the window. Names get mangled differently each time, so fuzzy.
2. **Time proximity** — extend the incident while activity continues.
3. **Channel migration** — "switch to channel 22" in a transcript extends the incident onto another channel. Detect and follow.
4. **Position proximity** when two candidates both have stated coordinates.

```toml
[correlator]
quiet_period_s        = 300   # no activity → close incident
vessel_match_ratio    = 0.80
max_incident_duration_s = 3600
```

Rules:

- **Incidents stay open and accrue.** Don't finalize at first detection.
- **Alert once on open, then update in place.** Fifteen buzzes for one mayday trains people to silence the app.
- **Escalate severity, never auto-de-escalate.** WATCH → CRITICAL on a later explicit mayday, and re-alert. The reverse requires a human.
- **Expose merge and split in the UI.** Correlation will be wrong sometimes; a two-tap manual merge beats a cleverer algorithm, and the merges are labeled training data.

### 5.7 Alerting

```python
class AlertChannel(Protocol):
    name: str
    async def send(self, incident: Incident, is_update: bool) -> AlertResult: ...
    async def healthcheck(self) -> bool: ...
```

| Channel | Role |
|---|---|
| `ConsoleAlertChannel` | Dev. Always available. |
| `MacOSAlertChannel` | Dev on Mac — `osascript` notification. Zero setup, useful immediately. |
| `PushoverAlertChannel` | Real alerting. Emergency priority bypasses Do Not Disturb and re-alerts until acknowledged. **Best fit for this project.** |
| *(later)* `GPIOAlertChannel` | Local buzzer/strobe. Depends on nothing — no internet, no carrier, no cloud. |

**The notification body must be fully actionable with no connectivity.** Cell coverage drops offshore; assume the recipient may never load the web page. Include severity, time, vessel, nature, position, and a verbatim quote in the message itself. The incident page is the rich layer, never the only path.

Routing by severity. Escalation-until-acknowledged is Pushover's emergency priority (retry/expire on priority 2), not a second router-level timer — see `docs/decisions.md` D16. **Deliberately do not build SMS for the POC** — US A2P 10DLC registration takes days to weeks and will block you. Pushover works in ten minutes.

### 5.8 Store

SQLite, WAL mode. Schema in `store/schema.sql`, tables per the report's data model: `transmission`, `transcript`, `dsc_message` *(stub, unused for now)*, `detection`, `incident`, `incident_member`, `annotation`, `alert`, `share_token`, `access_log`, `health_event`.

- **Archive Opus** (16 kHz mono, ~16–24 kbps) for storage; **generate an AAC/M4A copy at ingest** for playback. Opus-in-WebM on iOS Safari has been unreliable and the crew is on iPhones. Transcode at ingest, not on demand — no transcoding in the critical path.
- **SHA-256 every audio file at ingest**, store alongside the path.
- **Append-only.** Corrections go in `annotation` with author and timestamp. Never `UPDATE` a transcript or summary. Enforced at the database layer: `schema.sql` has `BEFORE UPDATE` triggers that abort updates to `transcript`, `annotation`, and archived `transmission` rows (the one allowed transmission update is filling in `audio_path`/`audio_sha256` at archive time).
- `transmission` and `transcript` stay separate — you will re-transcribe the archive with better models and want to compare.

### 5.9 Watchdog

Silent failure is the worst outcome. The rule, from D24: **when something is stuck, exit non-zero and let the supervisor restart us.** In-process recovery is more code and more ways to half-work, and half-working looks healthy.

Built (2026-10-09):

- **Capture heartbeat** — frames must keep *arriving*; none for `capture_stall_s` raises `CaptureStalled`. Frames, not audio level: a quiet channel is quiet frames.
- **Stage stall** — ASR or detection inside one call for `heartbeat_timeout_s` raises `StageStalled` (`StageTracker`). "Busy too long", not "no output recently", because healthy stages are idle for hours.
- **No transmission in N hours** (`no_audio_alert_hours`, default 6) — catches what the above cannot: a radio powered off, tuned away, or with its antenna knocked loose delivers perfect frames of silence. Fires once per silent spell, re-arms on traffic.
- **Daily self-test** — one "alive" message per local day at `self_test_hour`, sent through the real delivery path, so its arrival also proves notifications work. Last-sent time is read from `health_event`, not memory.
- **"Started" notice**, throttled to once per 10 minutes so a crash loop cannot bury it.
- **Disk-space check** (`min_free_disk_gb`).
- **Noise-floor drift** against the calibrated value, once per run.

All health notices go through the **outbox** (`alerting/outbox.py`, D28): queued, retried with backoff, FIFO, bounded, urgent items first, and a recovery notice reports any gap. A dead network can never stall transcription. Health notices are *not* incidents (D12) — `AlertChannel.send_health()`, `[alerting].channels_health`.

Not built: `WatchdogSec=`/`sd_notify`; per-stage queue-depth alerts (drops are counted and logged); recording retention (`retention_days` is configured but nothing deletes anything).

### 5.10 Web app

FastAPI + server-rendered templates for the POC. Separate process. Read-only against the store except for acknowledgments, annotations, and feedback.

Routes:
- `GET /` — incident list, newest first, severity-colored
- `GET /incidents/{id}` — the incident record
- `GET /transmissions/{id}/audio` — audio, range-request capable
- `POST /incidents/{id}/ack`
- `POST /incidents/{id}/feedback` — useful / not useful
- `POST /incidents/{id}/annotations`
- `GET /health` — pipeline status, last transmission time, noise floor
- `GET /live` — SSE stream of new transmissions

**Incident page priority order:** one-line status (severity, time, vessel, nature, position) → latest transmission playable in one tap → full chronological transcript with synced audio → summary, on the same screen as the transcript, never behind a tab → acknowledge and feedback controls.

**Render uncertainty honestly.** Low-confidence words in grey or italic. Per-transmission signal strength glyph. Which detection tier fired, in plain language. A visible count of segments dropped for low SNR. The reader must be able to tell which parts to distrust — that's the entire purpose of the record.

**The useful/not-useful control is not a nice-to-have.** It's how you build a labeled dataset of real traffic from the actual site, and it's worth more than any amount of prompt tuning. Build it in the POC.

A richer Next.js frontend, PWA install, offline caching, and chart view come later. Server-rendered HTML is the right POC choice.

---

## 6. Configuration

TOML via `pydantic-settings`, with env var override. `config/config.example.toml` committed; `config/config.toml` gitignored. **Secrets only via environment variables** — never in TOML, never committed.

```
VHFWATCH_PUSHOVER_TOKEN
VHFWATCH_PUSHOVER_USER
VHFWATCH_TELEGRAM_TOKEN      # bot token from BotFather
VHFWATCH_TELEGRAM_CHAT_ID    # the chat the bot posts to
VHFWATCH_ANTHROPIC_API_KEY
VHFWATCH_DEEPGRAM_API_KEY   # optional
```

---

## 7. Testing

- **Unit tests** for segmenter, watchword matcher, hallucination filter, correlator — all pure functions over fixtures, no hardware, no network.
- **Segmenter fixtures:** synthesize WAVs with known speech/silence patterns and assert exact boundaries. Cover: pause mid-transmission (must not split), sub-threshold blip (must not open), stuck carrier (must force-close), transmission starting at t=0 (pre-roll edge case).
- **Correlator tests:** synthetic detection streams asserting expected grouping, including channel migration and severity escalation.
- **`scripts/evaluate.py`** — run the pipeline over a labeled corpus and report precision, recall, and per-watchword false-positive counts. **This is the main tuning instrument.** Build it in Phase 1 and keep it working. Labels live in `data/labels.jsonl`, one object per line: `{"file": "<corpus wav>", "label": "distress" | "urgency" | "routine", "notes": "..."}`. A file counts as a predicted positive when any detection reaches URGENT or above.
- **Committed fixtures must be synthetic.** No real radio traffic in the repo, ever.

---

## 8. Sharing and publication

**Internal crew and department access:** the intended use. Access-token links from alerts, logged.

**A public live feed is out of scope and should stay that way.** Beyond privacy, a real-time public list of vessels in distress with coordinates is a lead-generation feed for opportunistic salvage and draws spectators and unqualified vessels toward active scenes. That's an operational hazard, and it's the kind of thing that gets a tool shut down permanently after one bad incident.

If public transparency is ever wanted: delayed (24–72 h) and redacted — no vessel names, no MMSI, position generalized to an area, no audio — or aggregate statistics only. Aggregate stats ("N incidents detected this quarter, median M minutes ahead of official notification") are also the numbers that justify the project's existence, so they're worth generating regardless.

---

## 9. Deliberately out of scope for the POC

Not because they're bad ideas — they're in the long-term plan — but because they'll sink the POC:

- SMS (A2P 10DLC registration blocks you for weeks; use Pushover)
- DSC and AIS decoding (needs an SDR; voice must stand alone anyway)
- Multi-channel scanning (one radio parked on 16; scanning creates gaps)
- Docker, Kubernetes, message brokers, microservices
- User accounts, roles, permissions (token links are sufficient)
- Native mobile apps (PWA later if needed)
- Cloud hosting (runs on the box; Tailscale for remote access)
- Speaker diarization, direction finding, RF triangulation
