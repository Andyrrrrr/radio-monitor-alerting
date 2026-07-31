# Code conventions

Rules and patterns that keep the codebase consistent across many independent agent sessions. Read this before writing code. `docs/architecture.md` says *what* to build; this says *how* to write it.

---

## 1. Language and style

- **Python 3.11+**, `asyncio` throughout the pipeline.
- **Type hints everywhere.** `mypy --strict` passes on `src/`.
- **`Protocol` for interfaces, not ABCs.** Structural typing keeps implementations independent and avoids an inheritance hierarchy nobody asked for.
- **`dataclass` for data, `Protocol` for behavior.** Core types live in `src/vhfwatch/models.py` — import from there, never redefine a shape locally.
- Format with `ruff format`, lint with `ruff check`. Both must be clean.
- **Docstrings on public functions explain _why_.** Restating the signature in prose is noise; the type hints already did that. Explain the non-obvious constraint, the unit, the failure mode.

```bash
ruff check src tests && ruff format --check src tests
mypy --strict src
```

## 2. Configuration

- **No hardcoded tunables.** Every threshold, timeout, window, and path comes from config. If you are typing a number into a function body, it belongs in `config.toml`.
  - The test for "is this a tunable?": would anyone ever want a different value on a different rig, site, or corpus? If yes, config.
  - Genuine constants (sample-rate conversion ratios, protocol identifiers) can stay in code. Name them.
- New config keys ship with a documented default in `config/config.example.toml`, including the unit and the reason the default is what it is.
- **Secrets via environment variables only** (`VHFWATCH_*`). Never in TOML, never committed. See `docs/architecture.md` §6 for the current variable list.
- `config/config.toml` and `config/watchwords.toml` are per-machine and gitignored. The `.example` files are the shared templates and must stay in sync with the code that reads them.

## 3. Identifiers and time

- **ULIDs for IDs**, not UUIDs — they sort chronologically, which matters constantly in a system whose entire data model is ordered by time.
- **All timestamps UTC and timezone-aware.** Never a naive `datetime`. Convert to local time only at the presentation layer: marine operations happen in local time, but the database is UTC.

## 4. Logging

- **`structlog` with structured key-value context**, not formatted strings.
- Always bind `transmission_id` / `incident_id` where applicable. You will be debugging this from logs alone, after the fact, on a machine you aren't sitting at.
- **Every discard gets logged with its reason.** Segments dropped for low SNR, transcripts rejected as hallucinations, queue overflow drops — all of it. Silent drops are how detections get lost invisibly, and the rejection log is also the review queue that tells you whether the gates are set right.

## 5. Platform rules

**The constraint:** development happens on Apple Silicon, deployment is x86 Linux. Every platform-specific dependency sits behind a Protocol with at least two implementations, selected by config.

**No `sys.platform` branching in business logic.** If you find yourself writing one, the right answer is another implementation of an existing Protocol plus a config key.

### Apple Silicon (development)

- ASR default is `mlx-whisper` — significantly faster than alternatives on M-series.
- `faster-whisper` works but is CPU-only on macOS (CTranslate2 has no Metal backend). Keep it working; it's the deployment path.
- Use `sounddevice`, not `pyaudio`. PortAudio via Homebrew.
- `MacOSAlertChannel` uses `osascript` for notifications — zero-setup dev alerting.

### x86 Linux (deployment target)

- ASR default is `faster-whisper`.
- `systemd` units with `Restart=always`. Pipeline and web app are **separate units** — a hung web request must never stop detection.
- ALSA/PulseAudio device naming differs from Core Audio, so `LiveAudioSource` config must accept a device name string, not just an index.

**Don't let macOS-only assumptions leak into the pipeline.** A Mac-specific dependency goes in the `[macos]` extra and behind an interface. Verify on Linux early (`docs/roadmap.md` Phase 4) — portability rot is much easier to prevent than to fix.

### Two development rigs

Andy runs a MOTU M2, Parker a Behringer UCA202 (`docs/decisions.md` D11). Both are just Core Audio input devices to the code — **keep it that way.** Device name, channel, gain, and thresholds all come from per-machine config.

Corpus audio is shareable between rigs. Calibration values are not — they're properties of one radio + cable + interface + knob position. Never hardcode a threshold that came from one person's setup, and check segmenter changes against both rigs' audio when you can.

## 6. Testing

- **Pure functions over fixtures.** The segmenter, watchword matcher, hallucination filter, and correlator take data in and return data out. Test them with no hardware, no network, and full determinism.
- **Synthesize segmenter fixtures programmatically** with known speech/silence patterns and assert exact boundaries. Required cases:
  1. Pause mid-transmission — must **not** split.
  2. Sub-threshold blip — must **not** open.
  3. Stuck carrier — must force-close at `max_duration_ms`.
  4. Transmission starting at t=0 — the pre-roll edge case.
- **Set `temperature=0.0` on ASR** so tuning runs are comparable between sessions. A tuning run you can't reproduce told you nothing.
- **`scripts/evaluate.py` is the main tuning instrument.** Build it in Phase 1 and keep it working from then on. Every threshold change should be justified by a before/after from it, not by how the change felt.
- **Committed fixtures must be synthetic.** No real radio traffic in the repo, ever (`AGENTS.md` constraint 8).
- Every safety rail added because of a real bug gets a named regression test. If you make something pure or injectable "so it's testable," write the test in the same session — otherwise you paid the design cost and got nothing back.

## 7. Adding a new implementation of a Protocol

The common task in this codebase. The recipe:

1. Add the implementation in the module its siblings live in (`asr/`, `alerting/`, `audio/`).
2. Register it in the config-driven selector — never at import time, never by platform sniffing.
3. Add the config key and its default to `config/config.example.toml`, with a comment saying which platforms it works on.
4. If it's platform-specific, put its dependency in the matching extra in `pyproject.toml`.
5. Note it in `docs/architecture.md` in the table for that interface.

## 8. Dependencies

Prefer the standard library. Every new dependency needs a justification in the commit message covering what it does that stdlib can't and what happens if it goes unmaintained. Platform-specific dependencies go in the `[macos]` / `[linux]` extras, never the base install.
