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
- **UFO202 chosen** for Parker: genuine line-level RCA inputs, no attenuator needed, class compliant, well-known quantity.

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

**Decided.** Andy on a MOTU M2, Parker on a Behringer UFO202. Both supported.

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

---

## D13 — stdlib `wave` (16-bit PCM only) for Phase 0 file I/O

**Decided for Phase 0.** `FileAudioSource` and the archival writer use the
standard library `wave` module and support only 16-bit PCM WAV, refusing
other formats with a clear error.

**Why:** everything Phase 0 touches — synthetic fixtures, the corpus
recorder's output, the archival placeholder — is 16-bit PCM we generate
ourselves, so `soundfile` would be a dependency with no current payoff
(docs/conventions.md §8). The Opus/AAC archive pipeline (§5.8) needs ffmpeg
anyway and lands in Phase 3 behind the same `archive_wav` call site.

**Revisit when:** Phase 2 live capture lands (recording and resampling make
`soundfile`/`soxr` genuinely useful), or the first time someone has a real
24-bit or float WAV they need to replay. Add the dependency then; do not
hand-roll 24-bit unpacking.

*(Partially revisited with Phase 2: `soxr` was added for capture-rate →
16 kHz resampling, exactly as this entry planned. File reading stays on
stdlib `wave`, 16-bit PCM only.)*

---

## D14 — Phonetic matching is classic Metaphone plus guards, not Double Metaphone

**Decided during Phase 1 implementation.** The architecture doc originally
said "Double Metaphone (`jellyfish`)", but jellyfish does not ship Double
Metaphone — only classic Metaphone. Rather than add a dependency, the
matcher uses classic Metaphone with two guards:

1. **Code equality relaxed to edit distance ≤ 1** ("mated" = MTT vs
   "mayday" = MT — the canonical mangling the phonetic tier exists for).
2. **A Jaro-Winkler similarity floor (`phonetic_min_similarity`, 0.70)**,
   because Metaphone codes are 2-3 characters and collide absurdly on
   their own ("mud" and "mayday" are both MT).
3. **Terms with tokens shorter than `phonetic_min_token_len` (4) skip the
   phonetic pass entirely** — "cpr" is one phonetic edit from "copy", the
   most common word on a radio. Exact and fuzzy matching still cover them.

**Why not add a double-metaphone package:** the available ones are
unmaintained or heavy, and there's no evidence yet that classic Metaphone
plus guards under-performs — that evidence would come from
`scripts/evaluate.py` on the real corpus.

**Revisit when:** evaluate.py shows mangled-keyword recall is materially
worse than the false-positive cost of loosening the guards.

---

## D15 — Waveform rendering is ~40 lines of vanilla canvas, not wavesurfer.js

**Decided during Phase 3 implementation.** The incident page draws
waveforms with WebAudio `decodeAudioData` + a canvas, with click-to-seek.

**Why:** wavesurfer.js would come from a CDN (violates local-first — the
record must render with the network unplugged) or be vendored into the
repo (a 100+ kB third-party artifact nobody here can review). The vanilla
version is small enough to read in one sitting and has no dependency to
rot.

**Revisit when:** the POC proves out and the web layer graduates to a real
frontend build (the "later" PWA in the roadmap) — vendoring becomes normal
practice at that point.

---

## D16 — No-ack escalation is Pushover emergency priority, not router logic

**Decided during Phase 3 implementation.** CRITICAL alerts go out at
Pushover priority 2, which re-alerts every `retry_s` until acknowledged or
`expire_s`. The router does not implement its own re-alert timer; the
planned `escalate_after_s` config key was removed rather than shipped
unused.

**Why:** two escalation mechanisms fighting each other is how people get
trained to silence the app (D12's failure mode). Pushover's is
server-side, survives the monitoring box dying mid-incident, and is
already acknowledged-aware.

**Revisit when:** a second real alerting channel (e.g. the GPIO buzzer)
needs escalation semantics of its own — that's when a router-level
mechanism earns its complexity.

---

## D17 — No high-pass filter on the capture path; the mains pickup is self-cancelling

**Decided during the 2026-08-12 M2 bring-up**, after measuring rather than
assuming. The idle line carries 60 Hz pickup roughly 16 dB above the voice
band, which inflates `calibrate.py`'s full-band noise floor and therefore
the derived `open_threshold_db`. A 200 Hz high-pass would remove it — and
we're not adding one.

**Why:** the hum is present *only when there is no signal.* With the
squelch closed the radio's output stage isn't driving the line, so it
floats and picks up the room's 60 Hz field; the moment the squelch opens
the amp drives it at low impedance and the pickup vanishes (measured at
−92 dB or lower, versus −65 dB idle, across three independent runs). It is
exactly anti-correlated with the thing being detected, so it can never
mask speech or cost real sensitivity. The full-band floor is also the
*correct* reference for a gate that has to clear the idle line.

Filtering would have added a config knob, a divergence between the level
the VAD sees and the level of the archived audio, and a DSP stage this team
would have to maintain — to solve a problem that measurement showed does
not exist. Note the first instinct here was wrong: the hum *looked* like it
was costing ~11 dB until its behaviour during transmission was checked.

**Revisit when:** a rig shows mains pickup that *persists through a
transmission* (a mains-powered base station, or a genuine ground loop —
§3.5's isolation-transformer case). Then the hum does compete with speech,
and a filter, or better an isolation transformer, is warranted. Also
revisit if open-squelch monitoring is ever adopted (§3.6), since a
continuously driven line behaves differently.

---

## D18 — On the M2 rig, gain staging is radio-high / trim-low, inverting §3.5's default

**Decided 2026-08-12, after measuring both splits.** `docs/hardware.md` §3.5
says to set the radio to roughly 1/3 and make up the level at the interface,
to keep the radio's speaker amp out of distortion. **On Andy's M2 rig that
advice is wrong and was measured to be wrong:** the correct setting is radio
volume **3/4** with the M2 input 2 trim at **90°**.

**Why:** the dominant noise on this rig is 60 Hz pickup induced on the cable,
which enters the chain *downstream of the radio's volume control.* The
interface trim therefore amplifies signal and pickup by the same amount and
cannot improve SNR; the radio's volume knob is the only stage that moves
signal without moving the interference. Shifting gain from the radio to the
interface raised the idle noise floor from **−68.4 to −38.0 dBFS** (+30 dB,
matched by +31 dB in both the mains and voice bands) while speech rose only
~8 dB, and introduced clipping (593 samples at full scale).

That floor is not merely untidy — it is disqualifying. At −38 dBFS the
derived `open_threshold_db` is −28, which lands *inside* the observed speech
range of −25 to −40 dBFS RMS, so the gate would miss a large fraction of
transmissions. At −68.4 every measured speech second cleared the threshold by
10 dB or more.

The distortion risk that motivated §3.5's ordering is real in general but
remains **unmeasured** on this radio at 3/4 volume; quantifying THD on speech
is awkward, which is why the guidance was precautionary in the first place. A
measured 30 dB penalty outweighs a hypothetical one.

**Revisit when:** the interference source is eliminated (a different cable
route, a shielded or balanced connection, an isolation transformer), at which
point the radio's own amp becomes the noisiest stage again and §3.5's default
ordering applies. Also revisit if transcription on strong local signals is
unexpectedly mushy with no other explanation — that would be the first actual
evidence of radio-stage distortion, and the fix is a *modest* reduction with
recalibration, not a wholesale shift of gain to the interface.

---

## D19 — Parker's rig runs provisionally on a 30 dB elevated floor, accepting weak-signal loss until it is diagnosed

**Decided 2026-09-07 by Parker, measured on "Parker's Calibration Machine"
(Behringer UFO202 + Kenwood NX-5200).** Measured floor is **−38.3 dBFS**, with the
measurement conditions owner-confirmed (radio in normal monitoring state,
nothing transmitting), against the M2 rig's **−68.1** — a 30 dB gap between two rigs running the
same radio model with the squelch closed on both. Thresholds are set from
the measured number and the rig is put into service now; the gap is logged
as a defect to chase, **not** as a property of the rig.

**What this is NOT:** it is not open-squelch hiss. The first reading of this
data assumed Parker's radio had no squelch and was therefore passing
continuous hiss — but `docs/bringup-log.md` 2026-08-12 records that the
NX-5200 has no accessible squelch control on *either* rig and sits **closed**
by default. Both rigs are in the same squelch state. The 30 dB is a
difference in the rest of the chain.

**Gain staging was the first hypothesis, and the knob position falsifies it.**
The guess was that the UFO202's missing input trim forced the radio's volume
far above Andy's 3/4, lifting the radio's own amp noise. Parker's knob is
**17 of 0–31** — *below* Andy's ≈23. Comparing dynamic range rather than
absolute level: Andy gets 60.5 dB peak-to-floor (−7.6 / −68.1), Parker gets
29.1 dB (−9.2 / −38.3). **31 dB less usable range with the radio turned down
further.** Radio amp noise scales with the volume setting, so a lower knob
should yield a relatively lower floor; it does not. The noise therefore
enters **downstream of the radio's volume control** — cable, UFO202, USB
power, or charger.

**This inverts the obvious fix, and makes the rig a second instance of D18
rather than its opposite.** `input_gain_db` sits downstream of the noise and
scales signal and noise together, so it cannot improve SNR — the same
argument D18 makes about the M2's trim. The radio's volume is again the only
stage that moves signal without moving the interference, so turning it *up*
helps; ADC headroom caps that at about 3 dB (peaks −9.2, ceiling −6). Worth
taking, nowhere near sufficient.

**That measurement was run, and it clears the interface.** UFO202 alone with
nothing on its inputs: **−88.8 dBFS** (p90 −88.7), against −38.3 with the
radio connected. −88.8 is the converter's own floor and 20 dB below the
entire M2 chain. Interface, USB power and host are eliminated; **all ~50 dB
enters from the radio side of the RCA cable.** Note this does not exonerate
the radio: noise injected into its output stage *after* its volume control —
a charger, typically — would not scale with the knob and matches the data.
The remaining split is charger vs. radio-output vs. cable pickup, and the
charger A/B (battery-only, then charging) separates the first from the rest
in two 30-second runs.

**A 13 dB crest factor (peak −25.4 vs RMS −38.3) says broadband noise, not
hum,** which is consistent with amp or converter hiss and inconsistent with
a ground loop. The charger A/B test is still unrun and remains the cheapest
discriminator.

**Why we ship on it anyway:** at −38.3 the gate sits near −31, and this rig's
speech peaks measure −9.2 with speech RMS therefore near −22 to −28, so
strong local traffic clears by 4 to 10 dB and segments correctly. What is
lost is **weak and distant stations** — §3.6's hardest-to-keep class.
`AGENTS.md` states that missed detections are acceptable and the user has
other notification paths, so a usable rig with a documented blind spot beats
a rig blocked on diagnosis. **This is a provisional setting with a known
cause to find, and it should not calcify into "how Parker's rig is."**

**Two deviations from the standard offsets, both measured, both deliberate:**

- `open_threshold_db` is floor **+7** (−31.3), not the usual +10. The +10
  heuristic exists to absorb floor *variability*; this floor measured
  **p90 == median, 0.0 dB of spread over 30 s**, so 7 dB is ample margin and
  the other 3 dB buys back sensitivity on a rig that has little to spare.
- Hysteresis is **4 dB**, not 6. At 6 dB the close threshold would land on
  −38.3 — exactly the floor — and the gate would never close, running every
  segment to `max_duration_ms`.

False opens are the cheap failure here: `min_duration_ms = 600` discards
short noise blips, and `min_snr_db = 3.0` rejects noise-only segments before
ASR, so a noise-triggered open costs a log line, not a hallucinated
watchword.

**Revisit when — and this one is expected to be revisited, not filed away:**
(a) the charger A/B test runs (battery+idle vs charging+idle vs
charging+transmission, reading the `voice` and `hum` bands separately);
(b) the radio volume knob position is recorded and compared against Andy's
3/4, and a lower-knob / `input_gain_db` split is measured against the
current one — measure both splits and compare idle floors, per §3.5's
instruction not to assume which stage dominates; (c) the UFO202 is
substituted or the radio swapped, isolating which box carries the noise;
(d) `evaluate.py` on real corpus shows the miss rate is worse than "some
weak stations" — the assumption that speech RMS sits near −22 to −28 is
inferred from a single peak reading, not measured, and is the weakest link
above.

---

## D20 — Phonetic watchword guards tightened after real traffic; "mated" recall is the price

**Decided 2026-09-07**, from the first real Ch 16 traffic this project ever
processed. That single transmission produced **two CRITICAL watchword hits,
neither of which appears in the transcript**: `mayday` matched the word
**"my"** (0.80) and `going down` matched **"going to"** (0.915). Probing the
matcher afterwards found `my`, `made`, `maybe` and `media` all firing
`mayday`, and `going to` / `going in` firing `going down`. Those are among the
most common words on any radio channel.

This is the evidence D14 named as its own revisit condition, arriving from
real audio rather than from `evaluate.py`.

**Two distinct causes:**

1. **Whole-phrase similarity let one exact token carry a garbage one.**
   "going to" scored 0.915 against "going down" purely because "going"
   matched exactly. Fixed by requiring **every token** to clear
   `phonetic_min_token_similarity` (0.80) against its counterpart. Per token,
   "to" vs "down" fails and the phrase is rejected.
2. **The similarity floor was too low for 2-character Metaphone codes.**
   `phonetic_min_similarity` raised **0.70 → 0.85**.

**The cost is real, and it is the point of this entry: "mated" → "mayday" is
now missed.** That is D14's canonical example and the original justification
for the phonetic pass. Measured Jaro-Winkler against "mayday":

| Candidate | Score | Wanted |
|---|---|---|
| `may day` | 0.967 | keep ✅ |
| `mayde` | 0.893 | keep ✅ |
| `medday` | 0.800 | keep ❌ lost |
| `my` / `made` / `monday` | 0.800 | reject ✅ |
| `maybe` | 0.790 | reject ✅ |
| **`mated`** | **0.760** | **keep ❌ lost** |

**No threshold separates them.** "mated" scores *below* three of the false
positives, and "medday" ties exactly with "my", "made" and "monday" at 0.800.
Raising the floor to 0.85 was chosen because it clears the entire 0.800
collision cluster with margin.

`AGENTS.md` settles the direction: *missed detections are acceptable, the user
has other notification paths, so bias toward fewer false positives.* A matcher
that fires CRITICAL on "my" would be silenced within a day, which is D12's
stated failure mode.

**The phonetic pass still earns its place** — `mayde` (0.893) and `may day`
(0.967) get through, and there is a test pinning that. If a future change
leaves nothing passing it, delete the pass rather than keeping dead weight.

**Measured, not assumed:** all ten observed false positives are now silent,
including three verbatim transcripts from real captured traffic, while every
distress phrase still fires — those are exact matches and were never at risk.

**Revisit when:** `scripts/evaluate.py` runs on a hand-labelled corpus large
enough to measure what the raised floor actually costs in mangled-mayday
recall. Today's evidence is a handful of probe phrases and one real
transmission, which is enough to justify stopping the bleeding and not enough
to call 0.85 optimal. If real maydays are being missed phonetically, the
answer is probably a better phonetic algorithm (D14's rejected double
metaphone), not a lower floor — the floor is holding back "my".

---

## D21 — D17's hum test is necessary but not sufficient: check the gate threshold too

**Found 2026-09-10 on Parker's rig at the fire station.** The laptop was
plugged into mains rather than running on battery, and injected **8.9 dB** of
supply hum into the audio chain: idle floor **−31.8 dBFS** instead of −40.7.
The spectrum was unambiguous — 84% of the energy in mains harmonics with
**120 Hz dominant at 74.7 dB**, which is full-wave-rectified supply ripple,
not the 60 Hz-dominant ambient field pickup of D17. Unplugging the laptop
restored −40.7 (p90 == median) and dropped every harmonic: 180 Hz by 45 dB,
300 and 360 Hz by 24–29 dB, 120 Hz by 13 dB.

**D17's test was applied and passed — and the setup was still broken.** The
hum collapsed ~28 dB the instant a transmission opened the squelch (60 Hz
−36.4 → −64, 120 Hz −32.3 → −60, voice band −35.5 → −8). Exactly D17's
mechanism: high-impedance pickup on a muted output, shorted out when the
radio's output stage drives the line. It is anti-correlated with signal and
**can never mask speech**, so D17 correctly says no filter is warranted.

**But it broke the segmenter anyway, by a different route.** At −31.8 dBFS the
idle line sat **above** `open_threshold_db` (−34.1). The gate would never
close: one continuous segment, hum fed to Whisper, and the noise-floor drift
warning firing on a rig that is otherwise fine. On Andy's M2 the hum sits ~24
dB below the threshold, so this failure mode never appeared there and D17 had
no reason to consider it.

**So the hum test has two questions, not one:**

1. **Does it persist through a transmission?** (D17) If yes, it competes with
   speech — isolation transformer, or a filter.
2. **Does the idle floor stay below `open_threshold_db`?** (this entry) If no,
   the gate never closes, regardless of how harmless the hum is to
   intelligibility.

**Fix used:** run the laptop on battery. **That is a test-day answer, not a
deployment one** — a monitoring system cannot run on laptop battery
indefinitely. For permanent mains operation the answer is the one
`docs/hardware.md` §3.5 already names for mains-powered setups: a 1:1 audio
isolation transformer / ground-loop isolator inline on the RCA cable, roughly
$15. This is now the second independent measurement pointing at it — the
radio's own charger cost 7.4 dB on the same rig on 2026-09-07.

**Revisit when:** the rig runs on mains permanently, at which point the
isolator stops being optional. Also note `calibrate.py` gives no hint about
this: it reported a stable floor with p90 == median and no warning. A floor
can be perfectly stable and still be 9 dB too high.

---

## D22 — The gate measures a frequency band and a noise tail, not full-band RMS and a median

**Decided 2026-09-10**, after a mains-powered laptop put Parker's idle floor
above `open_threshold_db` (D21) and the obvious fixes turned out to be wrong
in two separate ways.

### Part 1: gate on the voice band, archive everything

`segmenter.gate_band_hz` (default `[]` = full-band, so no existing rig
changes; Parker's rig sets `[300.0, 3400.0]`).

Sub-300 Hz hum cannot mask speech — D17 established that it collapses ~28 dB
the instant the squelch opens — but it *can* sit above the open threshold and
hold the gate open forever. A marine VHF channel carries voice in roughly
300–3000 Hz, so energy outside that band is never signal. Measured on real
audio:

| | Full-band gate | Voice-band gate |
|---|---|---|
| Idle, laptop on mains | −32.1 | **−39.4** |
| Idle, clean | −38.9 | **−49.2** |
| Speech | −16.2 | −16.7 (**0.5 dB cost**) |

**A high-pass filter was considered and rejected.** It measured slightly worse
(−39.8 vs −39.4 on the hum), needed a hand-written biquad because `scipy` is
not a declared dependency, and carried filter state across frames. Band
energy is stateless, vectorised, and says what it means.

**The archived audio is never filtered.** It is evidence (AGENTS.md), and a
human listening back must hear what the radio produced. Only the gate and the
noise-floor estimate see the band. `Transmission.rms_dbfs` is therefore
reported in the gate's domain so `est_snr_db` doesn't mix domains and
silently inflate — but `peak_dbfs` stays on the raw audio, because clipping
happens at the ADC and a band-limited reading would hide it.

**This does not contradict D17.** D17 asked whether a filter helps
*intelligibility* and correctly answered no. This is a gate-path measurement
change for a reason D17 never considered.

### Part 2: `calibrate.py` was measuring the wrong window, and always had been

**The first attempt at Part 1 failed**, and the failure is the more valuable
half of this entry. Thresholds derived from the band-limited *median* still
jammed the gate: every segment ran to `max_duration_ms`.

Cause: **`calibrate.py` measured 100 ms windows while the segmenter gates on
`frame_ms` (20 ms) windows.** Short windows average less noise, so their tail
is far higher — the same idle line reads:

| Window | median | p90 | max |
|---|---|---|---|
| 100 ms | −40.0 | −40.0 | −39.9 |
| **20 ms** | −40.4 | **−37.0** | **−34.9** |

A close threshold of −36.8, derived from the median, sat *inside* that 5 dB
tail and was crossed ~1.5 times a second. Since the gate closes only after
`hang_ms` of **continuous** quiet, every crossing reset the timer and the gate
never closed.

**Two changes:**

1. `calibrate.py` now measures at `segmenter.frame_ms`, so the number it
   prints means the same thing the gate will measure.
2. Thresholds derive from the **tail**, not the median:
   `close = max_idle_window + 2 dB`, `open = close + 4 dB`. What matters for
   closing is the loudest window the idle line produces, not its average.

**This bug predates today and affects full-band rigs too.** It was invisible
while margins were wide. ⚠️ **Andy's M2 close threshold (−64.1) was derived
as median + 4 dB from a 100 ms measurement and has never been checked against
a 20 ms tail** — it may be sitting inside one.

**Revisit when:** `frame_ms` changes (the thresholds are tied to it now), or a
rig runs long enough to show idle windows louder than the 30 s calibration
sample saw — the tail is an extreme-value estimate, and 30 seconds of it is
thin. If segments start running to `max_duration_ms`, that is this failure
returning and the answer is a longer calibration run, not a lower threshold.

---

## D23 — No ground-loop isolator for now; the hum is only present when the line is muted

**Decided 2026-09-10**, after D21 found a mains-powered laptop injecting
8.9 dB of supply hum and D22 fixed the gate. The obvious next step was the
1:1 audio isolation transformer `docs/hardware.md` §3.5 recommends for
mains-powered setups (~$15). We measured what it would actually buy, and the
answer is: nothing that matters today.

**The hum exists only while the radio's output is muted.** Measured through a
real transmission on 2026-09-10:

| | Idle (squelch closed) | During voice |
|---|---|---|
| 60 Hz | −36.4 | **−64** |
| 120 Hz | −32.3 | **−60** |
| Voice band | −35.5 | −8 to −17 |

~28 dB of collapse the instant the squelch opens — D17's mechanism exactly:
high-impedance pickup on a floating line, shorted out when the output stage
drives it. **So the hum never appears in the audio the transcriber sees.**
Transcription quality on this rig is set by the RF path, not by the laptop's
power supply.

**A measurement error worth recording**, because it nearly produced the wrong
conclusion: a sample taken shortly after the voice ended was initially read as
"squelch open, no voice" and showed the hum apparently *not* collapsing. Its
spectrum was identical to the muted line — same harmonics, spectral flatness
0.010 vs 0.008 — because the squelch had already closed. **On this rig,
"shortly after a transmission" is not a squelch-open sample.** Check the
spectrum before assuming which state a quiet stretch is in.

**Why the isolator is not needed now:**

- The gate problem is solved by band gating (D22), not by removing the hum.
- Thresholds are calibrated to the worst case (laptop on mains), so the rig
  works in either power state with no re-tuning.
- The hum is absent from every transmission, so it costs no transcription
  accuracy.

**What is given up, stated so nobody assumes it is free:**

- **Headroom.** Band-limited idle floor is −39.8 against an open threshold of
  −28.9 — about **11 dB**. An isolator would restore ~13 dB of that. A
  different laptop, outlet or added charger could eat the margin and jam the
  gate again.
- `est_snr_db` is referenced to a hummy floor and so reads pessimistic. That
  errs safe, and the metric is already known not to measure intelligibility.
- The noise-floor drift warning fires whenever the power state changes.

**Revisit when:** (a) the rig runs **unattended** — an 11 dB margin that a
swapped power brick can eat is not acceptable for a system nobody is watching,
and that makes the isolator a Phase 4 item; (b) `calibrate.py` shows the
band-limited floor creeping toward −28.9 after any change to the power setup —
re-running it is the 30-second early warning; (c) the rig moves to a mains
supply with a worse ground path than this one.

---

## D24 — A stalled capture kills the process; the supervisor restarts it

**Decided 2026-10-09**, building toward the unattended week-long run.

**The failure this exists for:** on 2026-09-23 the USB interface vanished. The
pipeline **stayed alive, logged nothing for ~50 minutes**, and later flushed a
stalled buffer as a bogus transmission timestamped 47 minutes in the past. The
noise-floor drift warning did not fire, because it only fires when the estimate
**moves**, and a dead stream moves nothing. Only a side check — opening the
device directly — caught it.

**Decision:** the pipeline now runs a `capture_watchdog` task that asserts
audio **frames are still arriving**, and raises `CaptureStalled` if none arrive
for `[health].capture_stall_s` (default 30). That propagates out of the
TaskGroup and exits the process non-zero.

**Why crash instead of reconnect in-process.** `LiveAudioSource` already has
reopen-with-backoff logic, and it did not save us: the stream did not error, it
simply stopped delivering. In-process recovery means more paths, more states,
and more ways to half-work — and the half-working state is the dangerous one,
because it looks healthy. A crash plus `systemd Restart=always` is **one
mechanism that covers USB dropout, power loss and an unplugged cable**, and it
cannot half-work. The operator has said explicitly that missing transmissions
during a restart is acceptable; silent failure is not.

**Why frames, not audio level.** Silence on Ch 16 is *quiet frames*, not an
absence of frames — the channel is quiet for hours at a stretch (0.3–1.4
transmissions/hour). Gating on level would fire constantly; gating on frame
arrival is unambiguous. `stats.last_frame_at` is **monotonic**, so a clock step
cannot fake liveness.

**Scope, deliberately narrow:** armed for live sources only (`source.name ==
"live"`), and disarmed as soon as ingest finishes — a file source ending is not
a stall. The first draft got this wrong and the quiet-channel test caught it.

**What this does NOT cover,** and is still Phase 4 work: a radio that is
powered off or tuned away, an antenna knocked loose, or a channel changed by a
passer-by. All of those deliver perfectly good frames of silence. The
"no transmission in N hours" alert is what catches them.

**Revisit when:** restarts become frequent enough to lose meaningful traffic —
at which point the fix is to find out why capture keeps dying, not to soften
the watchdog.

---

## D25 — faster-whisper is viable for deployment: it recovers what MLX drops, and hallucinates more on noise

**Measured 2026-10-09** over **20 real transmissions (148 s)** — the Coast
Guard assistance case of 2026-09-23 plus the MCS 2000 bring-up traffic. First
time `faster-whisper` has ever processed real audio on this project; it was not
even installed in the dev venv until this session.

**Speed, same machine (M-series CPU, int8):** MLX **9.1× realtime**,
faster-whisper **3.2× realtime**. Both are far faster than needed at
~1 transmission/hour. Not a selection criterion.

**Agreement:** only **2 of 20 transcripts identical** — both of those the
cleanest recordings (SNR 54 and the one unambiguous sentence). On marginal
audio the engines diverge substantially, so **they are not interchangeable and
transcripts are not comparable across engines.** Any WER or precision/recall
baseline must record which engine produced it.

**faster-whisper is better where it matters most.** On two transmissions MLX
emitted only `"!"` and faster-whisper recovered real content, including
`"...off of Lopez Island, this is United States Coast Guard on channel 1-6"` —
**a position**, which is exactly the information a distress case turns on.

**And worse in a way the project already defends against.** On two
transmissions where MLX correctly produced nothing, faster-whisper produced
hallucination loops (`"Uh, uh, uh, …"`, `"sail, sail, sail, …"`) and took
**9.1 s and 6.0 s to decode 1.9 s and 2.5 s of audio** — runaway decoding, a
latency risk as well as a content one. `max_repeat_tokens = 3` catches both
cases before detection, which is the gate doing its job.

**Decision:** faster-whisper is fit for the x86 deployment host, as
`docs/conventions.md` §5 already assumed. The hallucination gate is
**load-bearing for it in a way it is not for MLX** — do not weaken
`max_repeat_tokens` without re-running this comparison.

**Open:** neither engine is validated against ground truth, because nobody has
hand-labelled `data/labels.jsonl`. This compares engines to each other, not to
what was actually said. The recordings all exist, so the labelling is still the
cheapest accuracy work available.

**Revisit when:** labels exist (then measure WER properly), or when the station
host is running and latency under real load can be measured on the N100 rather
than inferred from a Mac.

---

## D26 — Telegram carries the recording and the transcript in one message

**Decided 2026-10-09**, superseding Pushover as the primary carrier for this
site and replacing `scripts/notify_transmissions.py`.

**The problem.** Transcripts at this site are wrong in exactly the places that
matter — vessel names came through as "Sir Muggis", "Sir budget", "For budget"
and "For much assistance" for a single vessel; channel numbers and positions
mangle routinely. Then on 2026-10-09 a transmission at **58.7 dB SNR** was
unintelligible to **both ASR engines and to the operator's ear**. The recording
is the evidence and the transcript is a convenience layered on it (AGENTS.md);
an alert carrying only the convenience is the wrong way round.

**Why not Pushover plus a link.** Pushover attaches **images only**, so audio
has to live behind a URL. That URL points at the machine serving it, so it
works on station wifi and fails on cell data. Fixing that means Tailscale on
both ends, or exposing recordings publicly — more moving parts, and the second
is unacceptable for recordings of real people.

**Why Telegram.** `sendAudio` takes a **caption**, so one notification is the
recording plus the transcript, playable inline with a scrubber. Outbound HTTPS
only: no inbound ports, nothing exposed, works anywhere the host has internet.
Files are ~30 KB.

**What is given up, stated plainly:** Pushover's **emergency priority** —
bypassing Do Not Disturb and re-alerting until acknowledged — has no Telegram
equivalent. That mattered a great deal in D16. It does not matter *here*,
because the operator's phone is **never** on Do Not Disturb and is always set
to ring. **If that ever changes, this decision has to be revisited**, because
nothing else in the system will wake someone who has silenced their phone.
Pushover remains implemented and config-selectable for exactly that reason.

**Scope limit, enforced by configuration and stated in the channel's own
docstring:** this sends **audio off-box**. Marine Ch 16 is public broadcast
traffic and the operator has accepted that explicitly. The fire/EMS audio in
`data/corpus-fire/` is **not** — addresses and patient details are plausible
there. Never point this channel at a rig monitoring anything but marine.

**Rejections are sent, and labelled.** The bring-up script this replaces pushed
a hallucination (`! ! ! ! ! ! !`) to the phone with no indication the pipeline
had thrown it out. Per-transmission notifications now carry the gate's verdict
with them — a rejected transcript still reaches the phone, because the radio
really did hear something, but it says so.

**Off by default** (`notify_every_transmission = false`). It is a monitoring
aid, not the distress path, and a feature that sends audio off a machine should
never switch itself on.

**Revisit when:** the operator's phone habits change; a weeklong run shows the
message volume is too high to stay useful; or Telegram's availability becomes a
dependency worth escaping (ntfy, self-hosted, is the fallback).
