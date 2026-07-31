# Decisions

Settled questions with reasoning, so they don't get relitigated mid-build. If you're about to argue with one of these, the reasoning is here — reverse it deliberately and update this file rather than drifting.

---

## D1 — This is a hobbyist supplemental tool, not a safety system

**Decided.** Positioned explicitly and prominently as supplemental situational awareness for people who already receive notifications through official channels.

**Why:** the organizational path to making this an official notification system is an impasse, and the people involved are competent and diligent — the blocker isn't something better engineering solves. Meanwhile a hobby-grade system that becomes a load-bearing alerting path is a liability and safety problem.

**Consequences:**
- Missed detections are acceptable — the user has other notification paths. So bias toward **fewer false positives** over maximum recall.
- The `README.md` disclaimer is deliberate and blunt. Don't soften it.
- Success metric is *lead time over official notification*, not detection coverage.

---

## D2 — Voice-only; DSC and AIS are not prerequisites

**Decided.** The pipeline relies solely on voice transcription from a single channel.

**Why:** DSC (structured digital distress with MMSI and position) and AIS (vessel positions) are richer and more reliable signals, and an earlier draft prioritized them. But Parker's operational reality is that the vessels involved — small recreational craft, kayaks, jet skis, day fishermen — largely don't carry DSC-capable radios or AIS transponders. Voice on Ch 16 is what they actually use.

**Consequences:**
- Voice ASR accuracy is the critical path, not a nice-to-have.
- No RSSI available from a physical radio, so segmentation is audio-energy VAD (D5).
- Watchword robustness and hallucination filtering carry much more weight.
- DSC/AIS may be added later as supplements. Nothing may depend on them. `dsc_message` exists in the schema as an unused stub.

---

## D3 — Physical radio with a line-level USB interface

**Decided.** Kenwood NX-5200 → K1 audio adapter → USB audio interface → MacBook.

**Why:** it's the hardware available. An SDR would give RSSI, multi-channel monitoring, and DSC, and is the right long-term answer — but the POC uses what's on hand.

**Rejected along the way:**
- **MacBook 3.5 mm jack direct** — TRRS combo jack, not a line input. Needs the signal on the mic contact plus 20–30 dB attenuation plus a TRS-to-TRRS adapter, and macOS may never switch into headset mode. Too many identical-looking failure modes for someone debugging solo.
- **Cheap CM108-class USB dongles** — workable, but mic-level inputs needing an attenuator, and "line-in" labeling on budget devices is unreliable.
- **UCA202 chosen** for Parker: genuine line-level RCA inputs, no attenuator needed, class compliant, well-known quantity.

---

## D4 — Bluetooth is not a viable audio path

**Decided and closed.**

**Why, two independent reasons:**

1. **Role mismatch.** The radio's Bluetooth exists to talk to accessories — it acts as the audio gateway and expects to find a headset. **macOS cannot act as a Bluetooth audio sink** and won't present itself as a headset to another device. There's likely nothing to pair with in the needed direction. (A Linux box *can* be an A2DP sink via BlueZ, but land-mobile radio Bluetooth is typically HFP/HSP only, which is considerably fiddlier — and reason 2 still applies.)

2. **Headset audio processing would break the segmentation design.** HFP/HSP paths apply **AGC, noise suppression, and VAD**, often on both ends. Every one is hostile here:
   - **AGC destroys level calibration.** The whole VAD approach depends on a stable amplitude relationship between silence and speech. AGC pumps the noise floor up during quiet periods — precisely the signal the energy gate reads.
   - **Noise suppression attacks exactly the wrong thing.** Weak VHF signals *are* hiss with quiet speech riding on them. A denoiser trained on clean close-mic speech removes the hiss and takes the speech with it — losing the marginal distant transmissions that matter most.
   - **VAD and comfort-noise generation** can gate real content out entirely.

   Plus tandem coding (CVSD/mSBC atop already band-limited noisy narrowband FM) and silent connection drops on a system meant to run unattended for months.

**Corollary:** this reasoning also applies to the **Mac's built-in microphone**, which macOS voice-processes in some capture modes. Acceptable for building and testing the pipeline; **never for calibrating thresholds.**

---

## D5 — Audio-energy VAD with squelch closed

**Decided for the POC.** RMS gate with hysteresis, hang time, and a mandatory pre-roll ring buffer.

**Why:** no RSSI available (D2/D3). With squelch closed, inter-transmission audio is near-digital-silence, so an RMS threshold segments almost perfectly.

**Accepted cost:** signals too weak to break squelch are **completely invisible**, and those are the distant transmissions of most interest. Revisit with open squelch and Silero VAD once there's data. Log RMS and SNR on every segment so that revisit is evidence-based.

**Non-negotiable detail:** ~300 ms pre-roll ring buffer. Squelch clips the first syllable, and "Mayday" arriving as "ayday" is a thrown-away detection.

---

## D6 — Local-first ASR, cloud optional

**Decided.** `mlx-whisper` on Apple Silicon for development, `faster-whisper` as the portable deployment default, Deepgram optional.

**Why:** the listening and alerting path must work with the network unplugged. Local-first also sidesteps the question of whether shipping harbor radio traffic to a third-party API is appropriate.

---

## D7 — Pushover, not SMS

**Decided for the POC.**

**Why:** US A2P 10DLC registration is mandatory for application-to-person SMS, costs ~$44 plus monthly fees, and **takes days to weeks to approve** — it would block the POC. Unregistered traffic also gets silently carrier-filtered. Pushover works in ten minutes, and its emergency priority bypasses Do Not Disturb and re-alerts until acknowledged, which is a better fit anyway.

**Later:** a local buzzer/strobe on GPIO is the most reliable channel in the design — it depends on no internet, no carrier, no cloud.

---

## D8 — Single process for the pipeline; web app separate

**Decided.** `asyncio` coroutines with bounded queues in one process. The web app is its own `systemd` unit / process.

**Why:** Ch 16 is silent 97–99% of the time, so throughput is trivial and microservices buy nothing but debugging pain. But a hung web request must never be able to stop detection.

**Rejected:** Docker, message brokers, k8s, Celery.

---

## D9 — Append-only records

**Decided.** Corrections are `annotation` rows. Audio is SHA-256 hashed at ingest. Nothing overwrites a transcript or summary.

**Why:** an incident record from a real emergency may end up in an after-action review or a legal proceeding. Costs almost nothing now, impossible to retrofit onto a year of overwritten data.

---

## D10 — No public live feed

**Decided.** Internal crew and department access only.

**Why:** beyond privacy, a real-time public list of vessels in distress with coordinates is a lead-generation feed for opportunistic salvage and draws spectators and unqualified vessels toward active scenes. That's an operational hazard, and it's the kind of thing that gets a tool shut down permanently after one bad incident.

**If transparency is ever wanted:** delayed (24–72 h) and redacted, or aggregate statistics only.

---

## D11 — Two development rigs, per-machine calibration

**Decided.** Andy on a MOTU M2, Parker on a Behringer UCA202. Both supported.

**Why:** it's what each person has. Also a useful forcing function — it keeps device-specific assumptions out of the pipeline.

**Consequences:**
- `config/config.toml` is per-machine and gitignored; `config/config.example.toml` is the shared template.
- **Calibration values do not transfer.** Noise floor and thresholds are properties of one radio + cable + interface + knob position.
- **Corpus audio is shareable** for developing and testing segmentation, ASR, and detection — but not for deriving the other rig's thresholds.
- Segmenter changes should be checked against both rigs' audio.

---

## D12 — Incident is the top-level object

**Decided.** One real-world emergency = one `incident`, spanning many transmissions and channels. Alerts fire per incident, not per transmission.

**Why:** one mayday produces a dozen-plus transmissions over several minutes. Fifteen notifications for one event trains people to silence the app, which is worse than no system.

**Consequences:** incidents stay open and accrue; alert once on open then update in place; escalate severity but never auto-de-escalate; expose manual merge/split because correlation will sometimes be wrong.
