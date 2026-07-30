# CLAUDE.md

Working context for Claude Code on this repository.

## What this project is

`vhf-watch` monitors a marine VHF voice channel (Ch 16), segments transmissions, transcribes them, flags possible distress traffic, and sends a notification linking to a verifiable incident record containing the original audio and transcript.

**Read `docs/ARCHITECTURE.md` before writing code.** It defines the interfaces, data model, and concrete parameter values. `docs/HARDWARE.md` covers the physical setup. `docs/ROADMAP.md` has the phased task list — work from it in order. `docs/DECISIONS.md` records settled questions with reasoning; check it before proposing a different approach.

## Framing that shapes the code

This is an explicitly hobbyist, supplemental awareness tool. It is **not** a safety-certified system and must never be positioned as one. Practical consequences for implementation:

- **Missed detections are acceptable; the user has other notification paths.** So bias toward fewer false positives over maximum recall.
- **Silent failure is the worst outcome.** A system that stops working without saying so is more dangerous than no system, because people have started relying on it. Health monitoring is a feature, not ops overhead.
- **Every automated conclusion must be verifiable by a human.** Original audio and raw transcript are always retained and always reachable from an alert. The LLM summary is a convenience layered on evidence, never a replacement for it.
- **Don't soften the disclaimer in `README.md`.** It's deliberate. If you touch that section, keep it blunt.

## Critical constraints

1. **Voice-only.** DSC and AIS are better signals, but the small craft this targets don't carry the equipment. Nothing may depend on them.
2. **No RSSI available.** A physical radio exposes audio, not signal strength. Segmentation is audio-energy VAD.
3. **Dev on Apple Silicon, deploy on x86 Linux.** Every platform-specific dependency sits behind a Protocol with ≥2 implementations. No `sys.platform` branching in business logic — select implementations via config.
4. **The pipeline must run identically over files and live audio.** `FileAudioSource` and `LiveAudioSource` are interchangeable. This is what makes offline development and deterministic tests possible.
5. **Local-first.** Listening, detection, and alerting must work with the network unplugged. Cloud ASR and LLM calls are optional upgrades that degrade gracefully.
6. **The alert path never depends on the LLM.** If classification fails or times out, the alert still goes out with the Tier 1 result and the raw transcript.
7. **Append-only records.** Corrections are `annotation` rows. Never `UPDATE` a transcript or summary.
8. **Never commit audio or `data/`.** It contains recordings of real people in real emergencies, and this is a public repo. Test fixtures must be synthetic.

## Conventions

- **Python 3.11+**, `asyncio` throughout the pipeline.
- **Type hints everywhere.** `mypy --strict` on `src/`. Use `Protocol` for interfaces, not ABCs.
- **`dataclass` for data, Protocol for behavior.** Core types live in `src/vhfwatch/models.py` — import from there, don't redefine.
- **No hardcoded tunables.** Every threshold, timeout, and path comes from config. If you're typing a number into a function body, it belongs in `config.toml`.
- **`structlog` with structured key-value context.** Always include `transmission_id` / `incident_id` where applicable. You'll be debugging from logs alone.
- **ULIDs for IDs**, not UUIDs — they sort chronologically, which matters constantly here.
- **All timestamps UTC and timezone-aware.** Convert to local only at the presentation layer. Marine ops use local time; the database uses UTC.
- **Secrets via environment variables only** (`VHFWATCH_*`). Never in TOML, never committed.
- Format with `ruff format`, lint with `ruff check`.
- Docstrings on public functions explaining *why*, not restating the signature.

## Commands

```bash
# Setup (macOS)
brew install ffmpeg portaudio
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,macos]"

# Hardware bring-up
python scripts/audio_devices.py            # list input devices
python scripts/calibrate.py --seconds 30   # measure noise floor, suggest threshold

# Corpus collection — start this as early as possible
python scripts/record_corpus.py --out data/corpus --channel 16

# Run
python -m vhfwatch.pipeline --config config/config.toml
python -m vhfwatch.pipeline --source file --path data/corpus/xyz.wav
python -m vhfwatch.web                     # separate process, port 8080

# Tuning
python scripts/replay.py data/corpus/xyz.wav
python scripts/evaluate.py --corpus data/corpus --labels data/labels.jsonl

# Checks
pytest
ruff check src tests && ruff format --check src tests
mypy --strict src
```

## Platform notes

**Two development rigs (`docs/DECISIONS.md` D11):** Andy runs a MOTU M2, Parker a Behringer UCA202. Both are just Core Audio input devices to the code — keep it that way. Device name, channel, gain, and thresholds all come from per-machine `config.toml`, which is gitignored.

**Apple Silicon (development):**
- ASR default is `mlx-whisper` — significantly faster than alternatives on M-series.
- `faster-whisper` works but is CPU-only on macOS (CTranslate2 has no Metal backend). Keep it working; it's the deployment path.
- Use `sounddevice`, not `pyaudio`. PortAudio via Homebrew.
- `MacOSAlertChannel` uses `osascript` for notifications — zero-setup dev alerting.

**x86 Linux (deployment target):**
- ASR default is `faster-whisper`.
- `systemd` units, `Restart=always`. Pipeline and web app are **separate units** — a hung web request must never stop detection.
- ALSA/PulseAudio device naming differs from Core Audio; `LiveAudioSource` config must accept a device name string, not just an index.

**Don't let macOS-only assumptions leak into the pipeline.** If you add a Mac-specific dependency, it goes in the `[macos]` extra and behind an interface.

## Testing expectations

- Segmenter, watchword matcher, hallucination filter, and correlator are pure functions over fixtures. No hardware, no network, fully deterministic.
- Synthesize segmenter fixtures programmatically with known speech/silence patterns and assert exact boundaries. Required cases: pause mid-transmission (must not split), sub-threshold blip (must not open), stuck carrier (must force-close), transmission starting at t=0 (pre-roll edge case).
- `scripts/evaluate.py` is the main tuning instrument. Keep it working from Phase 1 onward.
- Set `temperature=0.0` on ASR so tuning runs are comparable.

## Things that will waste your time if you don't know them

- **Whisper hallucinates confidently on noise and near-silence** — "Thank you for watching", repeated phrases. Unguarded, this fires watchwords. `detect/hallucination.py` is load-bearing, not defensive polish. Gate on SNR and duration *before* transcribing, and on `no_speech_prob` / `avg_logprob` after.
- **Pre-roll ring buffer is mandatory.** Squelch clips the first syllable. Without ~300 ms of pre-roll, "Mayday" arrives as "ayday" and you've thrown away the detection.
- **Hang time prevents fragmentation.** Without it, "Mayday, mayday, mayday — this is vessel Serenity" becomes five unusable 1-second clips.
- **`warmup()` the transcriber at startup.** First-inference model load is multi-second; you don't want that on a real mayday.
- **Both interfaces present 2 input channels. Select channel 0 explicitly — never average or mix them.** Mixing a live channel with a silent one costs 6 dB and will quietly invalidate every VAD threshold you calibrated. Open with `channels=2` and slice channel 0.
- **Gain differs by rig.** The M2 has a hardware trim; the **UCA202 has none** (its front knob is headphone-output only), so the radio's volume knob may be the only analog control. `input_gain_db` in config is a post-capture digital stage for that case — it can fix "too quiet" but **cannot fix clipping**, which is destructive and upstream.
- **Calibration values are per-machine and don't transfer between rigs.** Never hardcode a threshold that came from one person's setup.
- **Program marine channels as analog FM, 25 kHz wide.** The NX-5200 is a digital-capable radio; wrong mode or narrow bandwidth gives quiet, distorted audio and much worse transcription.
- **Store Opus for archive, generate AAC/M4A at ingest for playback.** Opus-in-WebM is unreliable on iOS Safari and the users are on iPhones. Never transcode on demand in the request path.
- **Use bounded queues.** If transcription falls behind, drop the *oldest unflagged* segments loudly. Never block ingest; never drop something already flagged.

## Out of scope for the POC

Don't build these, and push back if asked to: SMS (A2P 10DLC registration blocks for weeks — Pushover instead), DSC/AIS decoding (needs an SDR), multi-channel scanning (gaps lose transmissions), Docker/k8s/message brokers, user accounts and roles, native mobile apps, cloud hosting, diarization, direction finding.

## When you're unsure

- **Tunable parameter?** Config file with the current value as default, documented in `config.example.toml`.
- **Platform-specific?** Protocol with two implementations, selected by config.
- **Might discard data?** Log the rejection with its reason. Silent drops are how detections get lost invisibly.
- **Touching the alert path?** Ask whether it still works with the network down and the LLM unreachable. If not, restructure.
- **Adding a dependency?** Prefer the standard library. Justify in the commit message.
