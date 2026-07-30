# vhf-watch

Monitors a marine VHF voice channel, transcribes transmissions, flags possible distress traffic, and pushes a notification with a verifiable incident record.

---

## ⚠️ READ THIS FIRST

**This is a hobbyist project. It is not a safety system. Do not rely on it.**

This software is an experiment built by volunteers in their spare time. It is not certified, approved, audited, or endorsed by any agency, standards body, or manufacturer. It has no uptime guarantee, no accuracy guarantee, and no support.

**It will fail. Specifically, it will:**

- **Miss transmissions.** Weak signals, squelch gaps, overlapping traffic, and speech the recognizer can't parse will all go undetected. A real mayday can pass through this system unnoticed.
- **Fail silently.** A disconnected cable, a crashed process, a full disk, or a dead radio battery can stop detection with no obvious sign that anything is wrong.
- **Raise false alarms.** Radio checks, routine hails, and speech recognition errors will produce alerts for things that are not emergencies.
- **Get the details wrong.** Transcripts of noisy narrowband FM audio are unreliable. Automatically generated summaries can misstate vessel names, positions, and the nature of an emergency. **Always read the raw transcript and listen to the original audio before acting on anything this system reports.**

**Appropriate use:** supplemental situational awareness for people who are *already* receiving emergency notifications through official channels. At best this buys a few minutes of head start on information that is arriving anyway.

**Inappropriate use:** as a primary, sole, or authoritative source of emergency notification. As a replacement for monitoring the radio. As a replacement for dispatch. As justification for reducing any existing watch, staffing, or notification practice.

If this system is the only thing standing between someone in distress and a response, the design has already failed.

### Legal and operational responsibilities are yours

- **Monitoring and recording radio traffic is regulated and varies by jurisdiction.** In the US, receiving marine VHF is generally unrestricted, but *divulging* or *publishing* the contents of intercepted communications is governed by 47 U.S.C. § 605 and other law. Recording distress traffic involving identifiable people and vessels raises privacy questions. **None of this is legal advice.** Understand your local law before operating this.
- **Do not transmit.** This is a receive-only application. If you are using a land mobile radio to monitor marine channels, program those channels as receive-only. Transmitting on marine VHF without appropriate authorization is illegal, and transmitting over distress traffic is dangerous.
- **Do not publish a live feed of distress traffic.** Beyond the privacy problem, a real-time public list of vessels in trouble with positions attached is a target list for opportunistic salvage and draws spectators and unqualified vessels toward active scenes. See `docs/ARCHITECTURE.md` § Sharing.
- **Never commit recordings of real radio traffic to this repository.** `data/` is gitignored. Keep it that way.
- If you work for an agency, **get your own organization's approval before operating this**, and do not let it become an official notification path without that approval in writing.

---

## What it actually does

```
handheld VHF radio (parked on one channel)
        ↓ audio cable
    USB audio input
        ↓
    voice activity detection → discrete transmission clips
        ↓
    speech recognition (local)
        ↓
    watchword matching (fuzzy + phonetic) → LLM classification
        ↓
    correlate related transmissions into one incident
        ↓
    notification + incident record page (audio + transcript + summary)
```

Voice is the only signal this project relies on. DSC and AIS are richer and more reliable data sources, but the vessels this is intended to help — small recreational craft, kayaks, jet skis, day fishermen — mostly don't carry DSC-capable radios or AIS transponders. Voice on Channel 16 is what they actually use, so voice is the load-bearing path here. DSC and AIS may be added later as supplements, never as prerequisites.

## Status

**Proof of concept.** Nothing here is deployed or operational.

## Requirements

- Python 3.11+
- A VHF receiver with an audio output, and a way to get that audio into a computer
- macOS (Apple Silicon or Intel) or Linux x86-64

POC hardware: Kenwood NX-5200 (already programmed with marine channels) → multi-pin→K1 audio adapter → a line-level USB audio interface (Behringer UCA202 or MOTU M2) → MacBook Pro. See `docs/HARDWARE.md` for cable specs per rig, level calibration, and a bring-up procedure that isolates each link in the chain.

## Documentation

| Doc | Contents |
|---|---|
| `CLAUDE.md` | Working context and conventions for Claude Code |
| `docs/HARDWARE.md` | Radio programming, audio chain, calibration, verification |
| `docs/ARCHITECTURE.md` | Module layout, interfaces, data model, design decisions |
| `docs/ROADMAP.md` | Phased milestones with exit criteria |
| `docs/DECISIONS.md` | Settled decisions and their reasoning |
| `docs/BRINGUP_LOG.md` | Per-rig hardware settings and verification results |

## License

MIT. See `LICENSE`. Provided without warranty of any kind — see the disclaimer above, which is not boilerplate.
