# Bring-up log

Date-stamped record of hardware settings, calibration values, and verification results. **Fill this in as you go, not afterward.** When detection silently degrades in three weeks, this is how you find out what changed.

Template below — copy the block for each session.

---

## YYYY-MM-DD — session title

**Who:** 
**Rig:** (Andy/M2 or Parker/UCA202)
**Location:** 
**Weather / time of day:** (affects propagation and traffic volume)

### Hardware state

| Item | Value |
|---|---|
| Radio | Kenwood NX-5200 |
| Channel programmed | 16 / 156.800 MHz |
| Modulation | analog FM |
| Bandwidth | 25 kHz wide |
| TX inhibited? | |
| Squelch | open / closed, setting |
| **Volume knob position** | (mark it with tape and note it) |
| Antenna | stock rubber duck / external — describe |
| Antenna location | |
| Battery / power | |
| Audio adapter | multi-pin → K1 2-pin |
| **Rig** | UCA202 / M2 |
| Cable to interface | 3.5mm→dual RCA / 3.5mm→dual 1/4" TS |
| Audio interface | |
| **Radio volume knob position** | (tape it and note it) |
| **M2 input trim** (M2 rig only) | (tape it and note it) |
| **macOS input slider** | position, or "greyed out" |
| `input_gain_db` in config | |
| Sample rate | 48 kHz |
| 48V phantom / Hi-Z (M2 only) | must be OFF / OFF |
| Host machine | |

### Calibration

| Measurement | Value |
|---|---|
| Noise floor, squelch **closed**, nobody transmitting (dBFS) | |
| Noise floor, squelch open (dBFS) — if you have squelch control | |
| Speech peak, typical (dBFS) | |
| Clipping observed? | |
| Phase cancellation ruled out? (M2 rig) | (see `docs/hardware.md` §3.4) |
| `open_threshold_db` set to | |

### Verification steps (`docs/hardware.md` §5)

| Step | Result | Notes |
|---|---|---|
| 1. Radio hears traffic by ear | ⬜ | |
| 2. Adapter passes analog audio | ⬜ | |
| 3. Mac sees interface (2 channels) | ⬜ | |
| 4. Signal present, not cancelling | ⬜ | |
| 5. Mac records radio audio | ⬜ | |
| 6. Segmentation fires correctly | ⬜ | |
| 7. Transcription recognizable | ⬜ | |
| 8. End-to-end alert | ⬜ | |

### Observations

- Traffic volume heard by ear over N minutes:
- Segment count produced by software over same period:
- Discrepancy and likely cause:

### What changed since last session

### What broke / what's still unknown

---

## 2026-08-12 — M2 rig: audio chain verified, level set, floor calibrated

**Who:** Andy
**Rig:** Andy/M2
**Location:** desk, indoors — ⚠️ TO CONFIRM
**Weather / time of day:** ~16:00 local (America/Los_Angeles)

First bring-up session for either rig. Steps 3, 4 and 5 of `docs/hardware.md` §5 pass. Steps 1, 6, 7, 8 not yet attempted.

### Hardware state

| Item | Value |
|---|---|
| Radio | Kenwood NX-5200 |
| Channel programmed | 16 / 156.800 MHz |
| Modulation | analog FM — **confirmed by the owners** (2026-08-12); these radios are known to be correctly programmed for marine channels |
| Bandwidth | 25 kHz wide — confirmed by the owners |
| TX inhibited? | ⚠️ NOT VERIFIED, and deliberately not tested — testing means transmitting. Do not press PTT on the monitoring radio |
| CTCSS/DCS | none — confirmed by the owners |
| Squelch | **no accessible control on this radio.** Closed by default; open-squelch measurements were not possible |
| **Volume knob position** | **3/4** — ⚠️ well above §3.5's ~1/3 guidance. See "Gain staging" below |
| Antenna | stock rubber duck (assumed) — ⚠️ TO CONFIRM |
| Antenna location | indoors at desk — ⚠️ TO CONFIRM |
| Battery / power | battery |
| Audio adapter | multi-pin → K1 2-pin |
| **Rig** | MOTU M2 |
| Cable to interface | 3.5 mm TRS → dual 1/4" TS, one leg used |
| Audio interface | MOTU M2, **input 2** (input 1 carries Andy's XLR mic) |
| **Capture channel** | `use_channel = 1` (input 2 → channel 1) |
| **M2 input 2 trim** | **90°**, after being reduced ~10.5 dB from its initial position during this session |
| **macOS input slider** | **none exists.** The M2 exposes no software-controllable input gain to macOS, so there is no slider to reset or to lose across reboots. One less thing to record — and unlike the UCA202 case in §3.5, not a lost gain stage, because the M2 has a hardware trim |
| `input_gain_db` in config | 0.0 (not needed — analog level is correct) |
| Sample rate | 48 kHz, set in Audio MIDI Setup; **survived a dock reconnect** |
| 48V phantom / Hi-Z | ⚠️ TO CONFIRM OFF on channel 2 |
| Host machine | MacBook Pro (Apple Silicon), M2 via Thunderbolt dock |

### Calibration

| Measurement | Value |
|---|---|
| Noise floor, squelch closed, nobody transmitting (dBFS) | **−68.1** (median 100 ms RMS; p90 within 0.3 dB). Measured **twice**, either side of the failed re-staging experiment, agreeing to 0.3 dB (−68.4 then −68.1) — the setting is reproducible by hand |
| Noise floor, squelch open | n/a — no squelch control |
| Idle voice band (300–3400 Hz) | −80.8 dB |
| Idle mains band (50–70 Hz) | −64.9 dB |
| Speech peak, typical (dBFS) | **−7.6** (loudest of 22 s; −8.4 on the earlier run at the same staging) |
| Speech RMS range, per second | −26 to −45 dBFS |
| Clipping observed? | **No** — 0 samples at/over −0.5 dBFS. Clipping occurred twice during the session (0.0 dBFS both times: once from the initial trim setting, once from the failed re-stage) and was resolved both times in the analog domain |
| Phase cancellation ruled out? | **Yes** — 45 dB idle→signal jump on channel 1 |
| `noise_floor_dbfs` set to | −68.1 |
| `open_threshold_db` set to | **−58.1** (floor + 10 dB) |
| `close_threshold_db` set to | **−64.1** (6 dB hysteresis) |

### Verification steps (`docs/hardware.md` §5)

| Step | Result | Notes |
|---|---|---|
| 1. Radio hears traffic by ear | ⬜ | Not formally done. One unidentified transmission was captured mid-calibration (see below), which is suggestive but not a substitute |
| 2. Adapter passes analog audio | ✅ | Implied by 4/5 passing |
| 3. Mac sees interface (2 channels) | ✅ | `M2`, 2 in, 48 kHz. Device **index shifted 3→1→3** across reconnects as the iPhone mic came and went — name-substring matching handled it, index-based selection would have broken |
| 4. Signal present, not cancelling | ✅ | 4a level in range; 4b confirmed by ch 0 vs ch 1 having completely different spectra |
| 5. Mac records radio audio | ✅ | 30 s captured to `data/bringup/level-test.wav` (**local only — never commit**). Verified numerically rather than visually: no flat line, no clipped square wave |
| 6. Segmentation fires correctly | ⬜ | Needs a 30-minute run during busy traffic |
| 7. Transcription recognizable | ⬜ | Whisper has still never run on real audio |
| 8. End-to-end alert | ⬜ | |

### Observations

**Mains pickup is present but harmless, and it must not be "fixed".** The idle line carries a 60.0 Hz component (mains band −64.9 dB, well above the −80.8 dB voice band). It is **not** a ground loop: it was identical on battery with the charger unplugged. It's high-impedance pickup on a *muted* output — with the squelch closed the radio isn't driving the line, so it floats and collects the room's 60 Hz field.

The critical part: **the hum disappears the instant the squelch opens**, dropping to −92…−100 dB, because the radio's output stage then drives the line at low impedance and shorts the pickup out. Confirmed across three independent measurements. So it is exactly anti-correlated with signal and can never mask speech.

Consequences, recorded so nobody re-derives them:
- A high-pass filter was considered and **rejected** — it would remove nothing that ever competes with speech. See `docs/decisions.md` D17.
- `calibrate.py`'s full-band floor is the *right* number for the gate threshold, hum included, because the gate must clear the idle line.
- `est_snr_db` will read **pessimistic** (the floor EMA tracks the hum). That errs safe — it can only make the hallucination gate more conservative. At 20+ dB of real SNR it's nowhere near `min_snr_db = 3.0`.

**Dock vs. direct USB-C: the dock is 12 dB quieter.** Voice-band idle noise measured −71.6 dB through the Thunderbolt dock vs −59.1 dB with the M2 connected directly to the laptop on battery. The mains component was identical in both (−56 dB), so this is broadband noise, not hum — most likely switching noise on the laptop's USB power rail reaching the bus-powered M2, which the dock's own supply doesn't pass through. **Unexplained but reproducible. Keep the dock.** (Absolute levels in this paragraph predate the trim reduction and aren't comparable to the calibration table above; the 12 dB *difference* is the finding.)

**Clipping was found and fixed.** The first keyed transmission hit **0.0 dBFS**. Trimming input 2 down ~10.5 dB brought peaks to −8.4 with zero clipped samples. Calibrating against a *nearby* radio at full quieting is deliberate — it's the loudest signal the system will ever see, so real traffic lands below it with headroom.

**A `calibrate.py` run was invalidated mid-measurement** by a transmission: median −67.4 but p90 −40.4, and the script's own >6 dB instability warning fired correctly. Re-running on a verified-quiet window gave −68.4 with p90 within 0.1 dB. The warning did its job; trust it and re-run rather than pasting the number.

**Gain staging: §3.5's default ordering was tried and measured to be WRONG on this rig.** Settled setting is **radio volume 3/4, M2 input 2 trim 90°** — see `docs/decisions.md` D18 before changing either.

§3.5 step 4 calls for the radio at ~1/3 with the interface trim making up the difference, to keep the radio's speaker amp out of distortion. That was attempted mid-session. Measured result:

| | radio 3/4, trim 90° | radio down, trim up |
|---|---|---|
| Idle floor | **−68.4 dBFS** | −38.0 dBFS |
| Idle mains band | −64.9 | −33.7 |
| Idle voice band | −80.8 | −49.5 |
| Peak | −8.4, **0 clipped samples** | 0.0, **593 clipped samples** |
| Speech RMS range | −24 to −48 | −13 to −46 |

Everything downstream of the radio's volume knob rose ~31 dB; speech rose only ~8 dB. **Reverted.**

**The mechanism, because it inverts the general advice:** the 60 Hz pickup is induced on the *cable*, downstream of the radio's volume control. The interface trim amplifies signal and pickup equally and so cannot improve SNR — the radio's volume knob is the only stage that moves signal without moving the interference. On this rig, radio high / trim low is correct.

The −38 dBFS floor is disqualifying, not just untidy: it forces `open_threshold_db` to −28, which sits *inside* the −25 to −40 dBFS speech range, so the gate would miss a large share of transmissions. At −68.4 every speech second cleared by 10 dB or more.

Radio-stage distortion at 3/4 remains **unmeasured** — quantifying THD on speech is awkward. A measured 30 dB penalty outweighs a hypothetical distortion risk. If transcription on strong local signals is later unexpectedly mushy, that's the first real evidence; respond with a *modest* reduction and recalibration, not by shifting gain to the interface.

**Also observed in the clipped recording:** one second contained 518 clipped samples with a *low* voice band (−30.5 dB), so that energy was not speech — most likely a squelch-tail or un-key transient at the end of a transmission. Worth watching during §5 step 6: a loud non-voice pop can open the gate and, with `hang_ms = 800`, produce a segment long enough to clear `min_duration_ms`.

**The level test was transmitted on "channel 7", which is a dedicated fire-department training channel** the radio is licensed for — confirmed by Andy. Not a marine allocation, no issue. The monitoring radio was returned to Ch 16 afterwards **without touching the volume knob or the trim**.

**A new operator tool was added:** `scripts/level_meter.py`. `calibrate.py` is a fixed-length batch, which is the wrong shape for setting a knob — you need live feedback. The meter shows rms/peak/latched-max plus the voice and mains bands separately, and prints a plain-language verdict on exit.

### Reception at this site — §4's warning confirmed

**Marine Ch 16 traffic is not receivable from Andy's house** with the stock rubber duck indoors. This is exactly what `docs/hardware.md` §4 predicted, and it is a hardware/siting limitation, not a software one.

What *is* receivable: **fire-department traffic rebroadcast from a large central station, on the radio's "Forge ch 3" memory.** Strong and reliable.

Consequences:

- **Phase 2's exit criterion can proceed on fire traffic.** Segmentation is audio-energy VAD and is indifferent to vocabulary; squelch-gated FM voice is squelch-gated FM voice. It exercises thresholds, hang time, pre-roll and fragmentation for real, and doubles as the first-ever real-audio ASR smoke test.
- **It does NOT validate weak-signal sensitivity.** A central-station rebroadcast arrives fully quieting; a distant small vessel is the opposite, and that's the class §3.6 says is easiest to lose. Segmentation *logic* verified ≠ sensitivity proven.
- **It does NOT validate the detector, and misreads easily.** "Mayday" is standard fireground vocabulary for a firefighter in trouble, so watchwords can legitimately fire on this traffic. Do not treat those as false-positive measurements — it's a different vocabulary domain. The marine-loaded `initial_prompt` may also hurt WER here.
- **Marine corpus collection is blocked at this site.** Store fire audio in a separate directory (`data/corpus-fire/`) so it never contaminates marine tuning, labelling, or `evaluate.py` runs.
- **Fire/EMS traffic is more sensitive than marine chatter** — addresses and patient details are plausible. `data/` is gitignored (constraint 8), and this corpus should not be shared as freely as the docs encourage for marine audio.

**The highest-value action for the project is now an antenna, not code** — §4's "best $25 in the project": a telescopic whip or mag-mount with an SMA adapter, in a window with a view of the water. Failing that, record the marine corpus somewhere with better siting even if that's not where the system will live. No software change competes with this.

### What changed since last session

First session. Before this, no rig had been verified at all.

### What broke / what's still unknown

- Knobs were restored to radio 3/4 / M2 trim 90° and re-verified: floor −68.1 (0.3 dB from the pre-experiment −68.4), peak −7.6, zero clipping. ⚠️ **Still need physically taping** — a recorded number tells you what to restore, it doesn't prevent the bump.
- ⚠️ **TX inhibit unverified** on the monitoring radio, and deliberately untested since testing it means transmitting. Do not press PTT on it.
- Radio programming (modulation, bandwidth, tone squelch) is **confirmed by the owners** and no longer treated as an open question.
- One transmission was captured during calibration but **not identified**. Unknown whether it was real Ch 16 traffic, the second radio, or something else. Step 1 (hear traffic by ear) is still the honest way to settle this.
- Traffic volume on Ch 16 at this location remains unmeasured — still the project's open question, and it sets the Phase 4 watchdog threshold.
- Reception quality with a stock rubber duck indoors is unknown and may be poor (§4). **Do not diagnose software before confirming the radio can hear anything.**
- `LiveAudioSource`'s USB disconnect/reconnect path still hasn't had a deliberate yank test.

---
