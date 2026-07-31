# Roadmap

Ordered milestones with exit criteria. Work top to bottom. Each phase produces something demonstrable.

**Two things run in parallel with everything else, starting today:**

- **Corpus collection.** Real Ch 16 audio accumulates in wall-clock time and cannot be rushed later. Get `record_corpus.py` working and start recording as soon as *any* audio input works. The corpus is what every tuning decision depends on. **Share it between rigs** — whoever has working audio first unblocks the other person's Phase 1 work. (Corpus transfers; calibration values don't. See `docs/decisions.md` D11.)
- **`docs/bringup-log.md`.** Date-stamped entries for hardware settings, calibration values, and what worked. When something breaks in three weeks, this is how you find out what changed.

---

## Phase 0 — Skeleton and file-based pipeline

**No hardware required.** Everything here runs against WAV files. If the audio chain isn't working yet (see `docs/hardware.md` §1), this is a full day of productive work.

- [ ] `pyproject.toml` with `[dev]`, `[macos]`, `[linux]` extras; `uv` for env management
- [ ] `src/vhfwatch/models.py` — all core dataclasses from `docs/architecture.md` §4
- [ ] `config.py` — `pydantic-settings`, TOML + env override; `config/config.example.toml` and `config/watchwords.example.toml`
- [x] `.gitignore` — **`data/`, `config/config.toml`, `*.wav`, `*.db` first**
- [ ] `store/schema.sql` + `store/db.py` — SQLite WAL, migrations, all tables
- [ ] `audio/sources.py` — `AudioSource` Protocol + `FileAudioSource`
- [ ] `audio/segmenter.py` — RMS gate, hysteresis, hang time, pre-roll ring buffer, min/max duration
- [ ] Segmenter unit tests with synthesized fixtures — the four required cases in `docs/conventions.md` §6
- [ ] `structlog` configured; `scripts/audio_devices.py`

**Exit:** `python -m vhfwatch.pipeline --source file --path tests/fixtures/sample.wav` segments a WAV into transmissions, writes them to SQLite with audio files on disk, and the segmenter tests pass.

---

## Phase 1 — Transcription and detection, offline

Still no hardware. This is where accuracy gets established.

- [ ] `asr/base.py` Protocol; `asr/mlx.py` (dev) and `asr/faster_whisper.py` (portable)
- [ ] `warmup()` called at pipeline startup
- [ ] `detect/hallucination.py` — pre-ASR SNR/duration gate, post-ASR logprob gate, artifact blocklist, repetition detection. **Log every rejection with its reason.**
- [ ] `detect/watchwords.py` — normalize, exact, Double Metaphone (`jellyfish`), fuzzy (`rapidfuzz` ≥ 0.82)
- [ ] `detect/classify.py` — LLM with structured `IncidentSummary` output, "never infer" prompt, preceding-transmission context, 5 s timeout, **graceful degradation to Tier 1 on failure**
- [ ] `incidents/correlator.py` — grouping, open/accrue/close, severity escalation without auto-de-escalation
- [ ] `scripts/replay.py` and `scripts/evaluate.py`
- [ ] Hand-label the corpus into `data/labels.jsonl`

**Exit:** `evaluate.py` runs over the labeled corpus and reports precision, recall, and per-watchword false-positive counts. You have measured WER for `base.en` / `small.en` / `medium.en` on real audio and picked one on evidence. One real conversation collapses into one incident, verified by replaying recorded traffic.

**This is the phase that determines whether the project is viable.** If precision is unusable here, no amount of Phase 3 polish fixes it.

---

## Phase 2 — Live audio

Hardware enters. Work through the verification procedure in `docs/hardware.md` §5 — don't debug software before Step 4 passes.

- [ ] `LiveAudioSource` via `sounddevice`, surviving device disconnect/reconnect without killing the process
- [ ] Channel selection via `use_channel` — slice, never average
- [ ] `input_gain_db` applied post-capture, with a startup warning above ~12 dB
- [ ] Native-rate capture → 16 kHz mono float32 resample
- [ ] `scripts/calibrate.py` — measure noise floor, suggest `open_threshold_db`, write to config
- [ ] Startup noise-floor check warning on >6 dB drift from calibrated value
- [ ] Clipping detection and warning
- [ ] `scripts/record_corpus.py` — continuous timestamped recording, rotating files
- [ ] Bounded queues with oldest-unflagged drop policy and loud logging

**Exit:** the radio parked on Ch 16 produces segmented transmissions that appear in the database with sane boundaries, and a 30-minute run's segment count roughly matches what you heard by ear.

---

## Phase 3 — Alerting and the incident record

Now it becomes usable by a person.

- [ ] `alerting/base.py` Protocol; `console.py`, `macos.py` (osascript), `pushover.py`
- [ ] `alerting/router.py` — severity routing, alert-once-then-update-in-place, escalation on no-ack, dedupe
- [ ] **Notification body fully actionable with zero connectivity** — severity, time, vessel, nature, position, verbatim quote in the message itself
- [ ] Signed per-recipient access tokens; store hashes, log access
- [ ] `web/app.py` FastAPI, **separate process**, all routes from `docs/architecture.md` §5.10
- [ ] Incident page in priority order: status line → latest transmission one-tap playable → full transcript with synced audio → summary adjacent to transcript → ack + feedback
- [ ] `wavesurfer.js` waveform, click-to-seek, matched watchwords marked
- [ ] **Uncertainty rendered honestly** — low-confidence words greyed, signal-strength glyph, which tier fired, count of dropped segments
- [ ] AAC/M4A playback copies generated at ingest
- [ ] SHA-256 of audio at ingest, displayed on the record
- [ ] **Useful / not-useful feedback control** — this is the labeled-data engine, not a nice-to-have
- [ ] Annotations as append-only rows

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
