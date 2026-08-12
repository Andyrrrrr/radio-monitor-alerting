# Roadmap

Ordered milestones with exit criteria. Work top to bottom. Each phase produces something demonstrable.

**Two things run in parallel with everything else, starting today:**

- **Corpus collection.** Real Ch 16 audio accumulates in wall-clock time and cannot be rushed later. Get `record_corpus.py` working and start recording as soon as *any* audio input works. The corpus is what every tuning decision depends on. **Share it between rigs** — whoever has working audio first unblocks the other person's Phase 1 work. (Corpus transfers; calibration values don't. See `docs/decisions.md` D11.)
- **`docs/bringup-log.md`.** Date-stamped entries for hardware settings, calibration values, and what worked. When something breaks in three weeks, this is how you find out what changed.

---

## Phase 0 — Skeleton and file-based pipeline

**No hardware required.** Everything here runs against WAV files. If the audio chain isn't working yet (see `docs/hardware.md` §1), this is a full day of productive work.

- [x] `pyproject.toml` with `[dev]`, `[macos]`, `[linux]` extras; `uv` for env management
- [x] `src/vhfwatch/models.py` — all core dataclasses from `docs/architecture.md` §4
- [x] `config.py` — `pydantic-settings`, TOML + env override; `config/config.example.toml` and `config/watchwords.example.toml`
- [x] `.gitignore` — **`data/`, `config/config.toml`, `*.wav`, `*.db` first**
- [x] `store/schema.sql` + `store/db.py` — SQLite WAL, migrations, all tables
- [x] `audio/sources.py` — `AudioSource` Protocol + `FileAudioSource`
- [x] `audio/segmenter.py` — RMS gate, hysteresis, hang time, pre-roll ring buffer, min/max duration
- [x] Segmenter unit tests with synthesized fixtures — the four required cases in `docs/conventions.md` §6
- [x] `structlog` configured; `scripts/audio_devices.py`

**Exit:** `python -m vhfwatch.pipeline --source file --path tests/fixtures/sample.wav` segments a WAV into transmissions, writes them to SQLite with audio files on disk, and the segmenter tests pass.

---

## Phase 1 — Transcription and detection, offline

Still no hardware. This is where accuracy gets established.

- [x] `asr/base.py` Protocol; `asr/mlx.py` (dev) and `asr/faster_whisper.py` (portable)
- [x] `warmup()` called at pipeline startup
- [x] `detect/hallucination.py` — pre-ASR SNR/duration gate, post-ASR logprob gate, artifact blocklist, repetition detection. **Log every rejection with its reason.**
- [x] `detect/watchwords.py` — normalize, exact, phonetic (classic Metaphone + guards — see `docs/decisions.md` D14), fuzzy (`rapidfuzz` ≥ 0.82)
- [x] `detect/classify.py` — LLM with structured `IncidentSummary` output, "never infer" prompt, preceding-transmission context, 5 s timeout, **graceful degradation to Tier 1 on failure**
- [x] `incidents/correlator.py` — grouping, open/accrue/close, severity escalation without auto-de-escalation
- [x] `scripts/replay.py` and `scripts/evaluate.py`
- [ ] Hand-label the corpus into `data/labels.jsonl` *(needs corpus audio)*

**Exit:** `evaluate.py` runs over the labeled corpus and reports precision, recall, and per-watchword false-positive counts. You have measured WER for `base.en` / `small.en` / `medium.en` on real audio and picked one on evidence. One real conversation collapses into one incident, verified by replaying recorded traffic. *(Code side is in place, with the one-conversation-one-incident property pinned by tests over synthetic audio; the corpus-dependent measurements remain.)*

**This is the phase that determines whether the project is viable.** If precision is unusable here, no amount of Phase 3 polish fixes it.

---

## Phase 2 — Live audio

Hardware enters. Work through the verification procedure in `docs/hardware.md` §5 — don't debug software before Step 4 passes.

- [x] `LiveAudioSource` via `sounddevice`, surviving device disconnect/reconnect without killing the process *(code written; reconnect behavior needs a hardware unplug test)*
- [x] Channel selection via `use_channel` — slice, never average
- [x] `input_gain_db` applied post-capture, with a startup warning above ~12 dB
- [x] Native-rate capture → 16 kHz mono float32 resample
- [x] `scripts/calibrate.py` — measure noise floor, suggest `open_threshold_db`, prints a paste-ready config snippet
- [x] `scripts/level_meter.py` — live rms/peak/max meter with voice and mains bands split out, for setting the analog trim by hand *(added 2026-08-12; `calibrate.py` is a batch tool and gives no live feedback)*
- [x] **`docs/hardware.md` §5 steps 3–5 pass on the M2 rig** *(2026-08-12: device and channel confirmed, cancellation ruled out, peaks −8.4 dBFS with zero clipping, floor −68.4 dBFS → measured thresholds in config)*
- [ ] §5 step 1 — confirm the radio hears traffic by ear, and spot-check the §2.1 programming *(skipped during the first session; do it before diagnosing any missed detection)*
- [ ] §5 step 6 — 30-minute segmenter run during busy traffic *(the exit criterion below)*
- [ ] Hardware unplug test for `LiveAudioSource` reconnect
- [x] Noise-floor drift warning (>6 dB from calibrated value, checked continuously while live)
- [x] Clipping detection and warning
- [x] `scripts/record_corpus.py` — continuous timestamped recording, rotating files
- [x] Bounded queues with oldest-unflagged drop policy and loud logging

**Exit:** the radio parked on Ch 16 produces segmented transmissions that appear in the database with sane boundaries, and a 30-minute run's segment count roughly matches what you heard by ear.

---

## Phase 3 — Alerting and the incident record

Now it becomes usable by a person.

- [x] `alerting/base.py` Protocol; `console.py`, `macos.py` (osascript), `pushover.py`
- [x] `alerting/router.py` — severity routing, alert-once-then-update-in-place, dedupe; no-ack escalation is Pushover emergency priority (`docs/decisions.md` D16)
- [x] **Notification body fully actionable with zero connectivity** — severity, time, vessel, nature, position, verbatim quote in the message itself
- [x] Signed per-recipient access tokens; store hashes, log access
- [x] `web/app.py` FastAPI, **separate process**, all routes from `docs/architecture.md` §5.10
- [x] Incident page in priority order: status line → latest transmission one-tap playable → full transcript with synced audio → summary adjacent to transcript → ack + feedback
- [x] Waveform (vanilla canvas — `docs/decisions.md` D15), click-to-seek; matched terms listed per transmission
- [x] **Uncertainty rendered honestly** — low-confidence words greyed, signal-strength glyph, which tier fired *(count of dropped segments waits for Phase 4's health events — nothing persists that yet)*
- [x] AAC/M4A playback copies generated at ingest
- [x] SHA-256 of audio at ingest, displayed on the record
- [x] **Useful / not-useful feedback control** — this is the labeled-data engine, not a nice-to-have
- [x] Annotations as append-only rows

**Exit:** a replayed recorded mayday produces a Pushover notification whose deep link opens an incident record with playable audio, synced transcript, summary, and working ack/feedback — on a phone. Kill the web app and confirm alerts still fire.

---

## Phase 4 — Reliability

The difference between a demo and something anyone should leave running.

- [ ] `health/watchdog.py` — per-stage heartbeats, alert on any stage going quiet
- [ ] **"No transmission in N hours" alert** (start at 6)
- [ ] Noise-floor drift monitoring against calibrated baseline
- [ ] Daily end-to-end self-test including alert delivery
- [ ] Disk space and queue-depth monitoring
- [ ] `GET /health` dashboard: last transmission, noise floor, queue depths, ASR latency, per-stage heartbeats
- [ ] `systemd` units for x86 Linux — pipeline and web as separate units
- [ ] **Verify on x86 Linux with `faster-whisper`.** Do this before you've built much more; portability rot is easier to prevent than to fix.
- [ ] Runbook: restart procedures, recalibration, common failure modes

**Exit:** the system runs unattended for a week. Unplug the audio cable and it tells you within the configured window. It runs on x86 Linux, not just the Mac.

---

## Phase 5 — Tuning on real operation

The phase that actually determines whether people keep it running.

- [ ] Review every incident with Parker; record useful/not-useful on all of them
- [ ] Retune thresholds against accumulated feedback labels
- [ ] Prune noisy watchwords, add missed phrasings observed in real traffic
- [ ] Merge/split controls in the UI, informed by how correlation actually failed
- [ ] Aggregate stats: incidents detected, false-positive rate, **median lead time vs. official notification** — the number that justifies the project
- [ ] Re-run `evaluate.py` on the grown corpus and compare against Phase 1 baseline

**Exit:** fewer than roughly one false CRITICAL per week, and Parker chooses to keep it running without being asked.

---

## Later, only if the POC proves out

Roughly in value order:

1. **Better antenna, mounted high.** Beats every software change available. See `docs/hardware.md` §4.
2. **Fixed-mount receiver + mains power + UPS** on a permanent x86 host.
3. **Second receiver on 22A**, where USCG moves traffic after initial contact.
4. **SDR** — whole marine band at once, plus RSSI-based gating, plus DSC and AIS as supplements. Note voice must still stand alone.
5. **PWA** — installable, offline caching, Web Push, chart view with nearby vessels.
6. **Local buzzer/strobe on GPIO** — depends on nothing, arguably the most reliable alert channel in the design.
7. **Aggregate statistics sharing** with dispatch or USCG, if the organizational picture ever changes.

---

## Anti-goals

Recorded so they don't get relitigated mid-build:

- **Not** a certified or primary alerting system. Ever.
- **No** public live feed of distress traffic (`docs/architecture.md` §8).
- **No** SMS in the POC — 10DLC registration blocks for weeks.
- **No** multi-channel scanning on one radio — gaps lose transmissions.
- **No** microservices, Docker, message brokers, or user accounts.
- **No** real radio recordings committed to the repo.
