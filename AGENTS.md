# vhf-watch — marine VHF distress monitoring

`vhf-watch` monitors a marine VHF voice channel (Ch 16), segments transmissions, transcribes them, flags possible distress traffic, and sends a notification linking to a verifiable incident record containing the original audio and transcript.

**Who's working on this:** a small team of non-developers building through agentic coding. Explain _why_, not just _what_. Prefer boring, idiomatic Python over clever solutions — the people maintaining this need to be able to read it. See **Working agreement** below before you write anything complicated.

## Docs index

Deep reference lives in `docs/`. This file is the map; the docs are the territory. **Read `docs/architecture.md` before writing any code.**

| Doc | Read it when |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | **First, before any code.** Module layout, interfaces, core dataclasses, and concrete parameter values |
| [`docs/roadmap.md`](docs/roadmap.md) | Starting a work session — the phased task list, in order, with exit criteria |
| [`docs/decisions.md`](docs/decisions.md) | Before proposing a different approach — settled questions and why they're settled |
| [`docs/conventions.md`](docs/conventions.md) | Before writing code — Python style, platform rules, testing expectations |
| [`docs/definition-of-done.md`](docs/definition-of-done.md) | Before calling any task finished |
| [`docs/hardware.md`](docs/hardware.md) | Touching audio capture, calibration, or the physical chain |
| [`docs/bringup-log.md`](docs/bringup-log.md) | Debugging "it worked yesterday" — per-rig settings and dated verification results |
| [`docs/status.json`](docs/status.json) | Orienting at the start of a session — what's built, what's not, what's broken |
| [`docs/plans/`](docs/plans/) | Working on a feature big enough to need a written plan first |

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

## Repository layout

```
AGENTS.md            ← this file (CLAUDE.md just points here)
README.md            ← human-facing, including the disclaimer
config/              ← *.example.toml templates; real config.toml is gitignored
docs/                ← all reference documentation (see index above)
src/vhfwatch/        ← the package
scripts/             ← operator tools: audio_devices, calibrate, record_corpus, replay, evaluate
tests/               ← fixtures are synthetic, always
data/                ← gitignored: audio, corpus, database
```

Full module-by-module tree with responsibilities: `docs/architecture.md` §2.

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

## Things that will waste your time if you don't know them

Each of these cost someone real time. Full reasoning is in the linked docs.

- **Whisper hallucinates confidently on noise and near-silence** — "Thank you for watching", repeated phrases. Unguarded, this fires watchwords. `detect/hallucination.py` is load-bearing, not defensive polish. Gate on SNR and duration _before_ transcribing, and on `no_speech_prob` / `avg_logprob` after. (`docs/architecture.md` §5.4)
- **Pre-roll ring buffer is mandatory.** Squelch clips the first syllable. Without ~300 ms of pre-roll, "Mayday" arrives as "ayday" and you've thrown away the detection. (`docs/decisions.md` D5)
- **Hang time prevents fragmentation.** Without it, "Mayday, mayday, mayday — this is vessel Serenity" becomes five unusable 1-second clips. (`docs/architecture.md` §5.2)
- **`warmup()` the transcriber at startup.** First-inference model load is multi-second; you don't want that on a real mayday.
- **Both interfaces present 2 input channels. Select channel 0 explicitly — never average or mix them.** Mixing a live channel with a silent one costs 6 dB and will quietly invalidate every VAD threshold you calibrated. Open with `channels=2` and slice channel 0. (`docs/hardware.md` §3.7)
- **Gain differs by rig.** The M2 has a hardware trim; the **UCA202 has none** (its front knob is headphone-output only), so the radio's volume knob may be the only analog control. `input_gain_db` in config is a post-capture digital stage for that case — it can fix "too quiet" but **cannot fix clipping**, which is destructive and upstream. (`docs/hardware.md` §3.5)
- **Calibration values are per-machine and don't transfer between rigs.** Never hardcode a threshold that came from one person's setup. (`docs/decisions.md` D11)
- **Program marine channels as analog FM, 25 kHz wide.** The NX-5200 is a digital-capable radio; wrong mode or narrow bandwidth gives quiet, distorted audio and much worse transcription. (`docs/hardware.md` §2.2)
- **Store Opus for archive, generate AAC/M4A at ingest for playback.** Opus-in-WebM is unreliable on iOS Safari and the users are on iPhones. Never transcode on demand in the request path. (`docs/architecture.md` §5.8)
- **Use bounded queues.** If transcription falls behind, drop the _oldest unflagged_ segments loudly. Never block ingest; never drop something already flagged. (`docs/architecture.md` §3)

## Out of scope for the POC

Don't build these, and push back if asked to: SMS (A2P 10DLC registration blocks for weeks — Pushover instead), DSC/AIS decoding (needs an SDR), multi-channel scanning (gaps lose transmissions), Docker/k8s/message brokers, user accounts and roles, native mobile apps, cloud hosting, diarization, direction finding.

Full list with reasoning: `docs/roadmap.md` § Anti-goals and `docs/architecture.md` §9.

## Working agreement

This project has no full-time developer on it. Before implementing a request, evaluate the complexity cost:

- If a feature needs significant custom code where a standard-library or well-known-package pattern exists, say so and propose the simpler path.
- If a request would meaningfully increase bug surface or maintenance burden, push back and explain why **before** writing code.
- Prefer fewer dependencies. Justify every new one in the commit message.
- The goal is a codebase that stays understandable and modifiable, not one that does everything asked of it.
- When you make a judgment call the requester couldn't have made themselves, say so plainly in your response — don't bury it in the diff.

## When something goes wrong

If a task produces a bug, breaks something that worked, or needs rework:

1. Fix the immediate issue.
2. Identify what rule or context was missing that let the mistake happen.
3. Add that rule to the right doc in `docs/` — or to this file if it needs to be always-loaded.

Every mistake should become a rule that prevents it happening again. That's how this doc set is supposed to grow.

## Maintaining docs

Before finishing any task:

1. If you changed architecture, interfaces, or the data model → update `docs/architecture.md`.
2. If you established a new pattern worth reusing → add it to `docs/conventions.md`.
3. If you settled a question, or tried something and rejected it → add an entry to `docs/decisions.md`, including what would have to change to make it worth revisiting.
4. If feature status changed, or you found something broken → update `docs/status.json`.
5. If you completed a roadmap checkbox → tick it in `docs/roadmap.md`.
6. If you changed hardware settings or ran a calibration → add a dated entry to `docs/bringup-log.md`.

A doc that describes something the code no longer does is worse than no doc. When you change behavior, grep the docs for its name before you close the task.

## When you're unsure

- **Tunable parameter?** Config file with the current value as default, documented in `config/config.example.toml`.
- **Platform-specific?** Protocol with two implementations, selected by config.
- **Might discard data?** Log the rejection with its reason. Silent drops are how detections get lost invisibly.
- **Touching the alert path?** Ask whether it still works with the network down and the LLM unreachable. If not, restructure.
- **Adding a dependency?** Prefer the standard library. Justify in the commit message.
- **Still unsure?** Ask. A question costs a minute; the wrong architectural guess costs a weekend.
