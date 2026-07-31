# Definition of done

Check this before calling any task complete. It exists because "it works on my machine, in the happy path, once" is the default failure mode of agent-written code, and because nobody on this project is going to catch it in review.

---

## Always

- [ ] `pytest` passes.
- [ ] `ruff check src tests` and `ruff format --check src tests` are clean.
- [ ] `mypy --strict src` is clean.
- [ ] No new hardcoded tunables. Every threshold, timeout, or path you introduced reads from config and is documented in `config/config.example.toml`.
- [ ] No secrets in any committed file. Secrets are `VHFWATCH_*` environment variables only.
- [ ] `git status` shows nothing from `data/`, no `.wav`, no `.db`, no `config/config.toml`.

## If you touched the pipeline

- [ ] It still runs identically over a file and over live audio — `FileAudioSource` and `LiveAudioSource` remain interchangeable.
- [ ] Queues are still bounded, and the drop policy still drops the **oldest unflagged** segment, loudly. Nothing already flagged is ever dropped, and ingest never blocks.
- [ ] Every new discard path logs its reason with `structlog`, including `transmission_id`.
- [ ] Timestamps you added are UTC and timezone-aware.

## If you touched the alert path

- [ ] **Does it still work with the network unplugged and the LLM unreachable?** If the answer is no, restructure — this is constraint 6, not a preference.
- [ ] The notification body is still fully actionable without loading the web page: severity, time, vessel, nature, position, verbatim quote in the message itself.
- [ ] One incident still produces one alert, then in-place updates. No re-alert storms.
- [ ] Severity can escalate but never auto-de-escalates.

## If you touched the store

- [ ] Still append-only. Corrections are `annotation` rows; nothing `UPDATE`s a transcript or summary.
- [ ] Audio is SHA-256 hashed at ingest and the hash stored with the path.
- [ ] Schema changes are in `store/schema.sql` with a migration, not applied by hand.

## If you touched detection or segmentation

- [ ] The four required segmenter fixture cases still pass (`docs/conventions.md` §6).
- [ ] `scripts/evaluate.py` still runs, and you have a before/after precision/recall number for the change. **A threshold change without a measurement is a guess.**
- [ ] Hallucination gating still runs before watchword matching, not after.

## If you touched platform-specific code

- [ ] It sits behind a Protocol with at least two implementations, selected by config.
- [ ] No `sys.platform` branching landed in business logic.
- [ ] Mac-only dependencies are in the `[macos]` extra.

## If you touched hardware settings or ran a calibration

- [ ] A dated entry is in `docs/bringup-log.md` with the rig, the knob positions, and the measured values. Not "afterward" — now.

## Docs

- [ ] Architecture, interface, or data-model change → `docs/architecture.md` updated.
- [ ] New reusable pattern → `docs/conventions.md` updated.
- [ ] Question settled, or approach tried and rejected → `docs/decisions.md` entry, including what would have to change to revisit it.
- [ ] Feature status changed or something is known-broken → `docs/status.json` updated.
- [ ] Roadmap item finished → checkbox ticked in `docs/roadmap.md`.
- [ ] You changed or removed a behavior → grep the docs for its name and fix the stale references.

## What "done" does not mean

- **Not** "the tests I wrote pass." Tests written alongside the code test what the code does, not what it should do. For anything on the detection path, the corpus is the judge.
- **Not** "it worked when I ran it once." This system is meant to run unattended for months.
- **Not** "the LLM said it looks right." Every automated conclusion in this project is supposed to be human-verifiable, and that includes yours.

## Report honestly

If something in this list is unmet, say so explicitly in your final response — which item, and why. A task reported as done with a silently skipped step is exactly the failure mode this project's framing calls the worst outcome.
