# Bring-up log

Date-stamped record of hardware settings, calibration values, and verification results. **Fill this in as you go, not afterward.** When detection silently degrades in three weeks, this is how you find out what changed.

Template below — copy the block for each session.

---

## YYYY-MM-DD — session title

**Who:** 
**Rig:** (Andy/M2 or Parker/UFO202)
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
| **Rig** | UFO202 / M2 |
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
| **macOS input slider** | **none exists.** The M2 exposes no software-controllable input gain to macOS, so there is no slider to reset or to lose across reboots. One less thing to record — and unlike the UFO202 case in §3.5, not a lost gain stage, because the M2 has a hardware trim |
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

## 2026-09-07 — Parker's Calibration Machine: level set and floor calibrated on an unsquelchable rig

**Who:** Parker
**Rig:** Parker/UFO202 — referred to as **"Parker's Calibration Machine"**
**Location:** Parker's fire station (all Parker/UFO202 sessions are here — the "desk" and "window" positions below are both inside it)
**Weather / time of day:** ~09:50 local (America/Los_Angeles)

First bring-up session for this rig. Level is set and the floor is measured.
The headline is a **30 dB elevated noise floor versus the M2 rig** (−38.3
vs −68.1) with the squelch closed on both, cause not yet diagnosed. The rig
is put into service on the measured number provisionally
(`docs/decisions.md` D19).

### Hardware state

| Item | Value |
|---|---|
| Radio | Kenwood NX-5200 (handheld) |
| Channel programmed | ⚠️ TO CONFIRM |
| Modulation | ⚠️ TO CONFIRM — should be analog FM |
| Bandwidth | ⚠️ TO CONFIRM — should be 25 kHz wide |
| TX inhibited? | ⚠️ NOT VERIFIED, and do not test it — testing means transmitting |
| Squelch | **no accessible control — same as Andy's NX-5200, and closed by default.** Both rigs are therefore in the same squelch state, which is what makes the 30 dB floor difference a chain defect rather than a hiss measurement |
| **Volume knob position** | **17 out of 0–31** (~55%), TAPED. Note this is *lower* than Andy's 3/4 (≈23 on the same scale, if it is the same scale — worth confirming), which is what rules out the radio's own amp as the noise source; see Observations |
| Antenna | ⚠️ TO CONFIRM — presumed stock rubber duck |
| Antenna location | ⚠️ TO CONFIRM |
| Battery / power | ⚠️ **TO CONFIRM — and it matters more here than anywhere else in this table.** Whether the radio was on the charger during these measurements is unresolved, and charger noise is the leading candidate for the high floor. See "What broke / still unknown" |
| Audio adapter | multi-pin → K1 2-pin |
| **Rig** | Behringer UFO202 |
| Cable to interface | 3.5 mm → dual RCA |
| Audio interface | UFO202, **RCA left** |
| **Capture channel** | `use_channel = 0` |
| **Interface trim** | **none exists** — the UFO202's front knob is headphone output only. The radio's volume knob is the only analog gain stage on this rig |
| **macOS input slider** | ⚠️ TO CONFIRM (§3.5 — check System Settings → Sound → Input; may be greyed out) |
| `input_gain_db` in config | 0.0 — not needed, analog level is correct |
| Sample rate | 48 kHz |
| 48V phantom / Hi-Z | n/a on the UFO202 |
| Host machine | MacBook (Apple Silicon) |
| Device name matched | `USB Audio CODEC` (substring match; resolved to index 2 this session) |

### Calibration

| Measurement | Value |
|---|---|
| Noise floor, squelch **closed**, nobody transmitting (dBFS) | **−38.3** (median 100 ms RMS over 30 s). **Measurement conditions owner-confirmed:** radio in its normal monitoring state, nothing transmitting. This is the baseline the rig operates from, not a one-off reading. ⚠️ Still **30 dB above the M2 rig's −68.1 in the same squelch state** |
| Noise floor, squelch open | n/a — no squelch control |
| 90th percentile | **−38.3 — p90 equals the median.** The floor did not move measurably over 30 s |
| Peak sample during "silence" | −25.4 dBFS (~13 dB crest over RMS, consistent with random noise rather than intermittent interference) |
| Speech peak, typical (dBFS) | **−9.2** (loudest of a 12.8 s meter session on a transmission) |
| Speech RMS range, per second | ⚠️ **NOT MEASURED** — inferred to be roughly −22 to −28 from the peak. This is the weakest number in the D19 reasoning and should be measured directly |
| Clipping observed? | **No** |
| Phase cancellation ruled out? | n/a — RCA is unbalanced by construction (§3.4 applies to the M2 only) |
| `noise_floor_dbfs` set to | −38.3 |
| `open_threshold_db` set to | **−31.3** (floor **+7**, not +10 — D19) |
| `close_threshold_db` set to | **−35.3** (hysteresis **4 dB**, not 6 — D19) |

### Verification steps (`docs/hardware.md` §5)

| Step | Result | Notes |
|---|---|---|
| 1. Radio hears traffic by ear | ⬜ | Not formally done |
| 2. Adapter passes analog audio | ✅ | Implied — a real transmission reached the meter at −9.2 dBFS |
| 3. Mac sees interface (2 channels) | ✅ | `USB Audio CODEC`, 48 kHz, resolved to index 2 by name substring |
| 4. Signal present, not cancelling | ✅ | Signal present on channel 0; cancellation is not a failure mode on unbalanced RCA |
| 5. Mac records radio audio | ✅ | 47 s captured to `data/bringup/20260907-171006Z-ch07.wav` (**local only — never commit**). Peak −8.4 dBFS, 0 clipped samples |
| 6. Segmentation fires correctly | 🟡 | Fired correctly on a *test* transmission — 2 segments, 0 discarded, 0 queue drops. The §5 step 6 exit criterion still needs a 30-minute run during real traffic |
| 7. Transcription recognizable | 🟡 | **Whisper ran on real radio audio for the first time in this project.** Speech was recognizable; call signs, position minutes and the POB count were not. See "First real ASR run" below |
| 8. End-to-end alert | ⬜ | |

### Observations

**The floor is 30 dB above the M2 rig's, with the squelch closed on both,
and that is an unexplained defect — not a property of this rig.** −38.3 here
vs −68.1 on Andy's M2, same radio model, same squelch state. The gate
therefore sits near −31 instead of −58, and weak and distant stations
(§3.6's hardest-to-keep class) will be lost.

**The first reading of this session got it wrong and the wrong version was
briefly written into the docs:** it assumed this radio passed continuous
open-squelch hiss. It does not — the NX-5200 has no squelch control on
*either* rig and sits closed by default (see the 2026-08-12 entry above).
Both rigs are in the same state, so the 30 dB lives in the rest of the chain.

**That hypothesis was gain staging, and the knob position falsifies it.**
The first guess was that the UFO202's missing input trim forced the radio's
volume much higher than Andy's, lifting the radio's own amp noise. Parker's
knob is **17 of 0–31**, *below* Andy's 3/4 (≈23). Comparing dynamic range
rather than absolute level:

| | Peak | Floor | Peak-to-floor |
|---|---|---|---|
| Andy/M2 | −7.6 | −68.1 | **60.5 dB** |
| Parker/UFO202 | −9.2 | −38.3 | **29.1 dB** |

31 dB less usable range *with the radio turned down further.* Radio amp noise
would scale with the volume setting, so a lower knob should give a relatively
lower floor. It does not. **The noise enters downstream of the radio's volume
control** — cable, UFO202, USB power, or charger.

**Consequence, and it inverts the obvious fix:** `input_gain_db` sits
downstream of the noise source and amplifies signal and noise equally, so it
cannot improve SNR here. This rig is in the *same* situation as D18's M2, not
the opposite one: the radio's volume is the only stage that moves signal
without moving the interference. Turning the radio *up* would improve SNR —
but ADC headroom caps that at roughly 3 dB (peaks are at −9.2, the ceiling is
−6), so it is worth taking and nowhere near a fix.

**The cable-pull test was run the same session and settles it: the UFO202 is
clean.** With nothing plugged into its inputs, floor **−88.8 dBFS** (p90
−88.7, peak −72.2) against **−38.3** with the radio connected.

| Configuration | Floor | p90 |
|---|---|---|
| Radio + cable + UFO202 | −38.3 | −38.3 |
| UFO202 alone, inputs open | **−88.8** | −88.7 |

−88.8 is the converter's own noise floor, and **20 dB quieter than the entire
M2 chain** (−68.1). The interface, its USB power, and the host are all
eliminated. **All ~50 dB enters from the radio side of the RCA cable.**

Two consequences:

- **This rig is not inherently limited.** There is a very clean interface on
  the end of it; a floor matching or beating Andy's is available in principle
  once the source is found. Worth chasing rather than accepting.
- **"Downstream of the volume control" includes *inside the radio*.** Noise
  injected into the radio's output stage after its volume control — a charger
  being the obvious candidate — would not scale with the knob and would look
  exactly like what was measured. The knob-position deduction narrowed the
  location; it did not exclude the radio itself.

**Next cut, and it is the question that started this whole line of work:**
reconnect exactly as before and measure on **battery with the charger fully
disconnected**, then again while charging. That splits the remaining 50 dB
between the charger and everything else (radio output, cable pickup, adapter)
in two 30-second runs.

Putting the rig into service on this number **was a deliberate call by its
owner** — strong traffic still segments — but it is provisional, and D19
records what would resolve it.

**The floor is extraordinarily stable: p90 equals the median.** That is what
justifies the tighter-than-standard +7 dB offset. A 30 dB elevated *stable*
floor is a very different problem from a 30 dB elevated *drifting* one — the
first costs sensitivity, the second would cost reliability too.

**`level_meter.py` reported the floor as −41.5, which was 3.2 dB optimistic.**
The meter only averages windows whose peak is below −30 dBFS
(`scripts/level_meter.py:225`) so that speech does not pollute the estimate.
On this rig the *noise itself* peaks at −25.4, so that filter was discarding
the louder half of the noise and reporting only the quietest slice.
**On a hissy rig, trust `calibrate.py`'s number, not the meter's summary line.**
The meter's idle-floor figure is a convenience for knob-setting, not a
calibration measurement.

**`calibrate.py`'s loud-floor warning did not fire, and that is not an
endorsement.** The warning triggers above −30 dBFS (`scripts/calibrate.py:86`);
−38.3 passes silently while still being 30 dB worse than the other rig. The
script has no way to know what "good" looks like for a rig it has never seen.

### First real ASR run (same session, ch 7 test transmission)

**Transmitted on channel 7** — a channel these radios are licensed for, matching
the precedent set on 2026-08-12. **Ch 16 was not used and must not be: it is the
international distress and calling frequency.** The monitoring radio stayed in
receive.

Recorded 47 s, of which one transmission at 25.1–36.5 s. Replayed through the
full pipeline with `scripts/replay.py --fast`. Result: **2 transmissions
segmented, 2 transcripts, 0 rejected pre-ASR, 0 rejected post-ASR, 0 discarded,
0 queue drops.** Model `mlx-whisper:small.en`, warmup 16.7 s (which is exactly
why `warmup()` at startup exists).

| Spoken | Transcribed |
|---|---|
| "Radio check, radio check" | "If I can check, radio check" |
| "test station one" | "position one" |
| "four seven degrees" | "47 degrees" ✅ |
| "three six minutes north" | *dropped entirely* |
| "one two two degrees" | "one, two, two screens" |
| "two one minutes west" | "one minute west" |
| "Three persons on board" | "persons on board" |

**Read this as a baseline on a known-bad chain, not as a verdict on the model.**
This rig is running 30 dB above the M2's floor with the cause still
undiagnosed. It is, however, the first real input to the base.en / small.en /
medium.en WER comparison that Phase 1 has been waiting on.

**It also makes the architecture's evidence rule concrete.** A human listening
to that audio hears "three six minutes north" without difficulty; the ASR
dropped it. Position and persons-on-board are precisely the fields a distress
alert turns on, which is why original audio must stay reachable from every
alert and the summary is never allowed to replace it.

**Fragmentation:** one spoken transmission produced two segments (2.18 s and
10.06 s). `hang_ms = 800` did not bridge the pause after "radio check". Not
harmful here, but it is the mechanism the docs warn about, seen for real —
worth watching during the step 6 run.

**A bug was found, and it is the important outcome of this session.**
`mlx-whisper` returns `avg_logprob` as **NaN** (not missing) on many segments —
4 of 5 here. `merge_segments` duration-weights them, so one NaN poisons the
whole transmission's score; at the gate `NaN < -1.0` is `False`, so
`detect/hallucination.py`'s `is not None` guard never fires and the check
passes silently; stored to SQLite the NaN becomes NULL, which hides the cause
at the far end. **The logprob half of the post-ASR hallucination gate is
inert**, and here it failed open on the *worse* of the two segments — the one
with a spurious "!" token and the mangled position. `no_speech_prob` still
works, so the gate is degraded rather than absent. Logged in
`docs/status.json` as high severity.

### What changed since last session

First session for this rig. Before this, `parker_uca202` had never been
verified end to end and its thresholds were theory-derived guesses.

### What broke / what's still unknown

- ⚠️ **The charger question that started this work is still unanswered.**
  Whether the radio was charging during these measurements was not recorded.
  Parker confirmed the radio was in its *normal monitoring state*, so whatever
  the charger was doing is part of the accepted baseline by definition — but
  that is not the same as knowing it costs nothing. If the rig normally
  monitors while charging, charger noise may be a removable component of the
  −38.3. **The A/B test — battery+idle, charging+idle, charging+transmission,
  reading the `voice` and `hum` bands separately — is still the single most
  valuable 10 minutes available on this rig.** If the floor drops materially
  on battery, redo this calibration and revise D19.
- ⚠️ **Andy's "3/4" is recorded as a fraction, not a step number.** The
  comparison against Parker's 17/31 assumes both radios use the same 0–31
  volume scale. Both are NX-5200s so this is very likely, but it has not been
  confirmed, and the amp-noise deduction below rests on it.
- ⚠️ **Speech RMS was never measured directly** — the −22 to −28 range that
  D19's "strong traffic clears by 4 to 10 dB" claim rests on is inferred from
  a single peak reading. Measure it on the next transmission.
- ⚠️ Radio programming (channel, modulation, bandwidth, tone squelch) is
  unconfirmed on this rig. Andy's was owner-confirmed; this one has not been.
  **Do not diagnose software before confirming the radio can hear anything.**
- Step 5 (a real recording on disk) has not been done — re-run the meter with
  `--record` to close it.
- `LiveAudioSource`'s USB disconnect/reconnect path still has not had a
  deliberate yank test on either rig.

---

## 2026-09-07 (afternoon) — Parker's rig: the receiver is deaf, and the audio chain is not the reason

**Who:** Parker
**Rig:** Parker/UFO202 ("Parker's Calibration Machine")
**Sessions:** 10:26–10:57 (30 min), 11:45–12:05 (20 min), 12:17–12:19 (2 min, no traffic)

Nothing was changed on the rig between these sessions.

### The comparison that matters

| Session | Heard by ear on a second handheld | Captured by the rig |
|---|---|---|
| 10:26, 30 min | 3 | **1** |
| 11:45, 20 min | 5 (incl. a readable exchange with "Corvesta") | **0** |
| **Total** | **8** | **1** |

The one transmission that did get through arrived hot enough to **clip at 0 dBFS**.
The rig appears to receive only signals far stronger than normal traffic. This is
**not a regression** — it is what the rig has been doing all along, and the first
session only looked like success because it happened to contain one very strong
station.

### What was ruled out, and how

- **The audio chain.** The idle floor is −38.1 dBFS, matching every session with
  the radio connected, versus −88.8 with the UFO202's inputs open. Cable,
  interface, host, capture code and thresholds are all fine.
- **Any change between sessions.** The two recordings are **spectrally
  identical** — same mains-dominant idle signature, same rolloff in every band,
  same DC, same peak character.
- **Faint speech buried under the noise.** Whisper was run over the entire empty
  20-minute session in 30 s chunks normalised to near full scale: **zero** speech
  segments. That is strong evidence, because this project's own notes record that
  Whisper hallucinates readily on noise — here it invented nothing. At 20 ms
  resolution there is not even a brief squelch blip (max excursion 2.0 dB).

### The physics that settles the interpretation

**FM squelch is binary and there is no "quiet speech" state.** When squelch opens,
the AF level is set by the volume control, *not* by signal strength — a weak
station gives full-level scratchy audio, not quiet audio. So absence of audio
means **the squelch never opened**, not that the signal was too weak to record.
No threshold, model or gain change recovers these.

### The lead that came out of it

**"No accessible squelch control" has been read throughout these docs as "squelch
is not adjustable." That is wrong for a commercial radio.** On the NX-5000 series
squelch level is a **per-channel codeplug parameter** — there is no knob because
it is set in programming. A tight programmed squelch on Ch 16 explains every
observation, including why a stock handheld a few feet away hears five
transmissions while this radio hears none.

**Free test, one button:** most Kenwood commercial portables have a **Monitor /
squelch-off** side key. Pressed during traffic — rushing noise *then*
transmissions means squelch level is the fault and it is a programming change
(§3.6 already treats squelch-open as a deliberate option); rushing noise and
nothing else means RF is not reaching the radio, and antenna/position is next.

### What broke / what's still unknown

- ⚠️ **§5 step 1 (radio hears traffic by ear) is STILL unticked on both rigs**,
  and this session is exactly the situation it exists to prevent. Two sessions of
  threshold, noise and ASR analysis were performed downstream of a receiver whose
  basic function had never been confirmed. All of that analysis is valid; it was
  just measuring a chain fed by a nearly deaf receiver.
- ⚠️ Radio programming on Parker's radio — channel, modulation, bandwidth, tone
  squelch, **and now squelch level** — remains unconfirmed. Andy's was
  owner-confirmed; this one never has been.
- ⚠️ The second radio is a different receiver in a different position, so part of
  the gap may be siting. The clean test is both radios side by side, then swapping
  antennas between them.
- The earlier conclusion "traffic volume is very low" was **wrong** and has been
  corrected in `docs/status.json`. The site has ample Ch 16 traffic. Do not use
  the 1-transmission-in-30-minutes figure for the Phase 4 watchdog threshold.

---

## 2026-09-07 (late afternoon) — RESOLVED: the radio was in the wrong place

**Who:** Parker · **Rig:** Parker/UFO202 · **Session:** 12:59–13:17 (18.2 min)

**The radio was moved from the desk into the window. Nothing else changed.**

| | Desk | Window |
|---|---|---|
| Heard by ear | 8 | 6 |
| Captured | **1** | **6** |

**1-of-8 became 6-of-6.** Every transmission the operator logged was captured.

### Why the earlier hypotheses were wrong

Both radios are the **same model**, both are fire-department radios with
**squelch fixed in the codeplug by the communications chief** (the operator is
not permitted to change it), and they were **side by side** when the morning
traffic came through. That eliminated siting, radio model, squelch programming,
tone squelch and channel — every candidate except the one that turned out to
matter.

The leading hypothesis before this session was **computer RFI desensing the
radio through the audio cable**. That is now unlikely: the audio noise floor
barely moved between positions (−38.1 desk → −37.7 window) while reception
transformed. If the cable were injecting noise into the receiver, moving the
radio a few feet without changing the cable run would not produce this.

**It was position.** At VHF, a few feet and a window frame is enough. Two
identical radios inches apart behave differently when one is against glass and
the other is in the middle of a desk.

### What the window position does NOT fix

- **Two of six transmissions arrived as 0.2 s blips** and were discarded by
  `min_duration_ms = 600`. One of them was the operator's 13:09 entry —
  *"towing a boat taking on water"* — the only genuinely distress-adjacent
  traffic all day. Loosening `min_duration_ms` would not rescue it: there are
  only 0.2 s of audio there, because the squelch opened and shut. **Marginal
  signals are still lost, and the loss is silent.**
- **Everything strong now clips.** Every captured segment logged
  `peak_dbfs 0.2`. This is now cheap to fix and was not before: speech lands at
  −8.7 to −10.5 dBFS RMS against a −38 floor, so there is **28 dB of SNR** and
  the radio volume can drop ~10 dB and still leave 18 dB of margin. Suggested:
  **volume 13–14 instead of 17**, then re-run `calibrate.py`.

### Real traffic, transcribed

> "Paging calling US Coast Guard, this is US Coast Guard, go ahead over."
> "Pushing the school, calling the Coast Guard. Yes, Coast Guard, go ahead, over."

"Coast Guard", "go ahead" and "over" are correct — real marine procedure
vocabulary. The mangled openings are almost certainly a vessel name.

**The NaN fix works on real audio.** All four transcripts carry real
`avg_logprob` values, and "Vessel, calling the vessel," at −1.03 was correctly
rejected by the post-ASR gate — the same gate that was silently inert this
morning.

### Actions

- ⚠️ **Mark the window position.** It is worth more than any other single change
  made today, and it is currently held by nothing but memory.
- ⚠️ Reduce radio volume to ~13–14 and recalibrate to stop the clipping.
- §5 step 1 (radio hears traffic by ear) is now effectively satisfied for this
  rig by the 6-of-6 comparison against a human listening on an identical radio.

---

## 2026-09-07 (evening) — recalibrated at volume 13 in the window position

**Who:** Parker · **Rig:** Parker/UFO202

Radio volume reduced **17 → 13** (of 0–31) to stop the clipping that appeared
once the window position made reception work. Recalibrated in place.

| | Desk, vol 17 | Window, vol 13 |
|---|---|---|
| `noise_floor_dbfs` | −38.3 | **−41.1** |
| p90 − median | 0.0 dB | **0.0 dB** (2 of 3 runs) |
| `open_threshold_db` | −31.3 | **−34.1** (floor + 7) |
| `close_threshold_db` | −35.3 | **−38.1** (floor + 3) |

**The first run was measured three times, and the first one lied.** Run 1 gave
median −41.1 with **p90 −37.3** — a 3.8 dB spread that sits *under*
`calibrate.py`'s 6 dB instability warning, so nothing fired. Two repeat runs
both gave p90 == median exactly. The first run caught a transient. **Do not
paste a single calibrate.py run into config without repeating it** — the
warning threshold is not tight enough to catch this, and a 3.8 dB spread would
have put `close_threshold_db` within 0.2 dB of the p90, where the gate
struggles to close.

**A quiet data point on where the noise comes from.** Dropping the volume 4
steps lowered the floor by only ~2.8 dB (−38.3 → −41.1). If the noise were
entirely downstream of the volume control (D19's conclusion), the floor
would not have moved at all. So *some* of it does scale with the radio's
volume — the picture is mixed rather than purely downstream. Not enough to
overturn D19, but worth knowing before the next attempt at the ~40 dB gap.

### ⚠️ NOT YET VERIFIED — needs real traffic

This calibration is measured on the idle line only. Two things are still
unknown and cannot be checked without a transmission:

1. **Whether the clipping is actually gone at volume 13.** Every capture at
   volume 17 logged `peak_dbfs 0.2`.
2. **Whether speech still clears −34.1.** At volume 17 speech landed at −8.7
   to −10.5 dBFS RMS. Volume 13 should put it near −15 to −18, leaving ~23 dB
   of SNR — but that is arithmetic, not measurement.

Both are answered by the next captured transmission. If speech now lands close
to the threshold, the volume reduction went too far and 14–15 is the compromise.

---

## 2026-09-07 (afternoon, 2h14m) — volume 13 verified; two new findings from real traffic

**Who:** Parker · **Rig:** Parker/UFO202, window position, volume 13/31
**Session:** 13:48–16:01 (2 h 14 min, 9 files, ~1.3 GB)

### Volume 13 verified — the clipping is gone

| | Volume 17 | Volume 13 |
|---|---|---|
| Clipped samples per event | 2 844 (0.99%), 6 698 (1.16%) | **49 (0.006%), 9, 41** |
| Speech peak (100 ms RMS) | −4.6 to −2.6 dBFS | **−9.9 dBFS** |
| Margin over `open_threshold_db` | — | **24 dB** |

A ~150× reduction, low enough not to affect transcription. **No need to drop
to 14 or 15.** Noise floor held at −40.7 to −41.1 across all nine files, and
the operator's 13:56 laptop-audio change had no measurable effect.

### Capture rate 2 of 8 — and volume 13 is NOT the cause

Each missed timestamp was checked directly. All six show voice-band lifts of
1.4–4.5 dB, and the definitively empty 11:45 session peaked at **4.4 dB on
pure noise** — so they are indistinguishable from noise. **The audio never
arrived; the squelch did not open.** If the volume reduction were responsible
we would see audio present but below the gate. We see nothing. The operator's
own notes agree — "clipped", "static", "couldn't understand".

### Finding 1: the hysteresis window straddles squelch-open hiss

Levels around the 13:59 broadcast: **−40.7 dBFS before, −19 during, −36.4 for
fifteen seconds after.** That −36.4 is squelch-open hiss — the radio stays
unmuted after the voice stops.

`close_threshold_db` is **−38.1**, *below* that hiss, so once the gate opens it
cannot close until the squelch itself closes. One spoken transmission became a
**59-second segment**. `open_threshold_db` (−34.1) is correctly *above* the
hiss, so hiss alone will not start a segment — but the hysteresis window sits
across it.

The calibrated floor (−41.1) is the **squelch-CLOSED** line. This rig has a
third level that calibration never sees: squelch-open hiss at ≈−36.4. Any
close threshold below it will hold segments open. Raising close to ≈−35.5 would
close promptly but leaves only ~1.4 dB of hysteresis, which is its own risk.
**Not changed yet — this needs more than one observation.**

### Finding 2: `est_snr_db` does not measure intelligibility

The 17-second broadcast the operator logged at 1:59 is **unintelligible to
Whisper** — it returned `'!'` and nothing else, across raw, normalised and
high-passed variants. Yet the audio *is* speech-shaped: broadband (top 12
spectral bins hold 0.97% of the energy) with an envelope modulation peak at
**3.11 Hz**, squarely in the syllabic range.

The pipeline scored it **`est_snr_db` = 16.3 dB.**

**In FM, once squelch opens the audio LEVEL is constant regardless of signal
strength — only the recovered audio's QUALITY degrades.** `est_snr_db` measures
level above the muted-line floor, so it is decoupled from intelligibility: a
hiss-dominated, unreadable transmission scores 16 dB and passes
`min_snr_db = 3.0` untouched. Every marginal transmission on this rig will do
the same.

Whisper emitted `'!'` rather than inventing a sentence this time. Given the
project's documented concern about hallucination on noise, that is luck, not a
guarantee. **This is a design gap, not a bug** — nothing is behaving contrary
to its specification. Written up before proposing any change to the gating.

---

## 2026-09-10 — NA-773 antenna installed; laptop mains supply found injecting 9 dB of hum

**Who:** Parker · **Rig:** Parker/UFO202, fire station, window position, volume 13/31
**Session:** 08:04:39 – 09:32:29 (1 h 28 min, 6 files)

**Only the antenna changed** since 2026-09-07 — same site, same window
position, same volume, same channel. Stock rubber duck → **Nagoya NA-773**.

### The antenna: encouraging, NOT proven

| | 7 Sep, stock duck | 10 Sep, NA-773 |
|---|---|---|
| Captured / heard by ear | 2 of 8 | **2 of 2–3** |
| Fragments (<0.6 s) | 2 of 3 | **0 of 2** |
| Event durations | 16.7 s, 0.6 s, 0.2 s | **28.4 s, 1.2 s** |
| Events per hour | 1.3 | 1.4 |

The operator logged three (08:04, 08:53, 09:02); recording started 08:04:39, so
the first is probably outside the window. Both captures were **full-length, not
fragments** — the metric predicted hardest to explain away.

**Two events is far too few to conclude anything.** A quiet channel produces
the same numbers as a good antenna. Traffic was light. Several more sessions
are needed before crediting the NA-773. The hum below does *not* confound this
— it affects the audio floor, not whether the radio hears anything.

### The laptop's power supply was injecting 8.9 dB of hum

Idle floor read **−31.8 dBFS**, against −40.7 to −41.1 on 7 Sep. Spectrum:
**84% of energy in mains harmonics, 120 Hz dominant at 74.7 dB** — full-wave
rectified supply ripple, not the 60 Hz-dominant ambient pickup of D17.

**Unplugging the laptop from mains restored −40.7** (p90 == median), dropping
180 Hz by 45 dB, 300 and 360 Hz by 24–29 dB, 120 Hz by 13 dB. Only 60 Hz
remains, down 6 dB — ordinary ambient pickup.

**−40.7 is within 0.4 dB of the −41.1 in config, so the existing calibration
still stands. No config change was needed.**

**Why this mattered even though the hum is harmless to speech:** it collapsed
~28 dB the moment a transmission opened the squelch (voice band −35.5 → −8),
so it can never mask speech and D17 correctly says no filter. But at −31.8 the
idle line sat **above** `open_threshold_db` (−34.1) — the gate would never
close. See `docs/decisions.md` **D21**: the hum test needs a second question,
"does the idle floor stay below the open threshold?", not just D17's "does it
survive a transmission?".

⚠️ **`calibrate.py` gave no hint.** It reported a stable floor, p90 == median,
no warning. A floor can be perfectly stable and still be 9 dB too high.

### Actions

- ⚠️ **Running the laptop on battery is a test-day fix, not a deployment one.**
  For permanent mains operation, a 1:1 audio isolation transformer /
  ground-loop isolator on the RCA line (~$15, `hardware.md` §3.5). This is the
  **second** independent measurement pointing at it — the radio's own charger
  cost 7.4 dB on this rig on 2026-09-07.
- Antenna comparison needs several more sessions before any claim.

---

## 2026-09-10 (later) — band-limited gate enabled so the laptop can stay plugged in

**Who:** Parker · **Rig:** Parker/UFO202, fire station, window, volume 13, NA-773

The operator needs the laptop on mains for extended runs, which puts 8.9 dB of
supply hum on the idle line (D21). Rather than depend on remembering to
unplug, the gate now measures the **voice band** instead of full-band RMS.

### Settings after this change

| | Before | After |
|---|---|---|
| `gate_band_hz` | (didn't exist) | **[300.0, 3400.0]** |
| `noise_floor_dbfs` | −41.1 full-band | **−39.8** band-limited, 20 ms, laptop ON MAINS |
| `open_threshold_db` | −34.1 | **−28.9** |
| `close_threshold_db` | −38.1 | **−32.9** |

**These numbers are NOT comparable to the old ones** — different measurement
domain. Calibrated to the **worst case (laptop plugged in)** deliberately, so
the rig works in either power state. On battery the band floor is ~−53, so the
drift warning will fire when the laptop is unplugged; that is the warning
working, not a fault.

### The first attempt failed, and that is the useful part

Thresholds derived from the band-limited *median* still jammed the gate — one
120-second segment closed by `max_duration`, exactly the failure the change was
meant to fix.

**`calibrate.py` was measuring 100 ms windows while the segmenter gates on
20 ms windows**, and had been since it was written. Short windows average less
noise, so the tail is much higher:

| Window | median | p90 | max |
|---|---|---|---|
| 100 ms | −40.0 | −40.0 | −39.9 |
| **20 ms** | −40.4 | **−37.0** | **−34.9** |

The close threshold sat inside that 5 dB tail and was crossed ~1.5 times per
second. The gate closes only after `hang_ms` of **continuous** quiet, so every
crossing reset the timer.

`calibrate.py` now measures at `frame_ms` and derives thresholds from the
**tail** (`close = loudest idle window + 2 dB`, `open = close + 4 dB`).

⚠️ **This affects Andy's rig too and has not been checked.** His
`close_threshold_db` (−64.1) came from a 100 ms median and may sit inside a
20 ms tail. Re-run `calibrate.py` on the M2 before trusting its segment
durations.

### Verified end to end

Replaying the recording that jammed now yields **29.5 s and 2.2 s segments,
both closed by `hang_time`**, matching the two real transmissions — instead of
one 120 s segment closed by `max_duration`.

Speech band-limited at 20 ms measures p1 −25.6 / p50 −16.2 dBFS, so 99.9% of
speech windows clear the −28.9 open threshold.

---

## 2026-09-15 — 6 h 37 m live watch: 2 of 2 against the ear log, and volume 13 has no headroom left

**Who:** Parker · **Rig:** Parker/UFO202, fire station, window, volume 13/31, NA-773
**Session:** 10:04:32 – 16:41:55 local (6 h 37 min, live pipeline, not `record_corpus.py`)

**Nothing was changed.** Same site, window position, volume, antenna and config
as 2026-09-10. The point of the session was a real-time ear-log comparison: the
operator logged what he heard, the pipeline reported what it captured, and the
two were compared as they happened rather than afterward.

### Result: 2 transmissions, 2 in the ear log, 2 captured

| Local | Dur | Voiced | rms | peak | `est_snr_db` | Transcript (small.en) |
|---|---|---|---|---|---|---|
| 10:16:00 | 2.2 s | 1.08 s | −17.1 | **−0.16** | 22.0 | "See you guys." |
| 15:00:49 | 4.8 s | 3.72 s | −13.3 | **−0.08** | 27.9 | "Last night, last night," |

Both closed by `hang_time`, neither fragmented, 0 pre-ASR rejects, 0 post-ASR
rejects, 0 queue drops, 0 detections (no watchword hits — correct).

**Capture rate 2 of 2, but that is two events, not a result.** The operator
logged nothing the pipeline missed, which is the metric that matters, and it is
the second session in a row with no fragments. It is still far too little to
credit the NA-773. 6 h 37 m for two transmissions is **0.3/hour** against
1.4/hour on 9/10 — and per the operator, **Ch 16 here is normally very quiet;
9/10 was the unusual session, not this one.** Do not read future silence as a
fault, and do not use 9/10's rate as the expected rate.

**The second transcript is probably incomplete**: 3.72 s of voiced audio
yielded four words, repeated. Consistent with the 9/07 finding that marginal
FM audio arrives at full level but low intelligibility — see `est_snr_db` 27.9
on a transmission Whisper could barely read. That finding now has a second
observation behind it.

### Volume 13 clips on both transmissions — headroom is effectively zero

Both events tripped `clip_warn_dbfs` (−0.5): peaks **−0.16 and −0.08 dBFS**.
Decoding the archived Opus, 0.03% and 0.05% of samples sit within 1 dB of full
scale — brief peaks touching the ceiling, not sustained distortion, and not
enough to hurt transcription. (Not directly comparable to 9/07's "clipped
samples" count, which measured at full scale, not within 1 dB.)

9/07 concluded "the clipping is gone" at volume 13 from nine files. That still
holds in the sense that matters — no gross distortion — but **2 of 2 loud
transmissions now touch the ceiling, so there is no margin for a louder or
closer station.** Clipping is destructive and upstream; `input_gain_db` cannot
undo it.

**Not changed today, deliberately:** dropping to volume 12 would have made the
session non-comparable with 9/10 mid-run, and it requires re-running
`calibrate.py` (the knob sets the floor as well as the peaks). Speech has room
to spare — 15:00 averaged −13.3 dBFS against a −28.9 open threshold — so one
step down is unlikely to cost detections. **Decide before the next session, not
during one.**

### Proving silence was real: a half-hourly idle reading

Six and a half hours with two events is mostly silence, and a quiet channel and
a dead audio chain look identical from the log. Every ~30 min, a separate 5 s
capture was band-limited to 300–3400 Hz and measured at 20 ms windows — the
same domain as the gate:

**−39.6 to −41.1 dBFS median across 9 readings, all within 1.3 dB of the
calibrated −39.8.** A disconnected radio or wrong `use_channel` reads far
lower. This is worth keeping as the standing check during a quiet watch;
`/health` and Phase 4 heartbeats do not exist yet.

**Power state changed mid-session and did not matter.** Started on battery
(10:04), plugged in by 11:05, AC for the rest. No `audio.noise_floor_drift`
warning fired in either state, and the idle readings above bracket both — the
band-limited gate (D22) is doing exactly what it was enabled for.

macOS sleep was blocked with `caffeinate -i -w <pid>` for the run. **Untested:
whether the pipeline recovers its capture stream after a sleep/wake cycle.**
Assume it does not until someone checks.

### Not exercised today

No `VHFWATCH_ANTHROPIC_API_KEY` and no Pushover credentials, so Tier 2
classification and push alerting were off for the whole session. Tier 1
watchwords ran and correctly fired nothing.

### Next session

- **Operator plans to move the rig closer to the water** for better reception
  and more complete transmissions. That changes site, so treat its numbers as
  a new rig state: re-run `calibrate.py` there and do not carry today's floor
  across (D11).
- Decide volume 12 vs 13 **before** starting, and recalibrate if it changes.
- Keep running the ear-log comparison. The useful number is transmissions the
  operator hears that the pipeline misses; totals mostly measure how busy the
  channel was.

---

## 2026-09-23 — Pushover proven end to end; a real Coast Guard case that fired no alert; and capture died silently for 50 minutes

**Who:** Parker · **Rig:** Parker/UFO202, fire station, window, volume 13/31, NA-773
**Session:** 08:06:45 – 17:31:21 local trustworthy (9 h 25 min), stopped 18:25
**Unchanged** from 2026-09-15: same site, window position, volume, antenna, config.

### 18 transmissions, 13 with words, 5 without

All captured whole, none fragmented, no queue drops. Full list in the database;
the shape of it:

| Local | Dur | `est_snr_db` | Transcript |
|---|---|---|---|
| 08:42:46 | 5.2 s | 26.8 | "Pission Con copy, this is United States Coast Guard…" |
| 08:43:01 | 11.0 s | 29.1 | "…channel 16 is for hailing and distress only…" |
| 12:51:33 | 1.9 s | 19.3 | *(empty — rejected)* |
| 13:43:51 | 2.5 s | 23.6 | "!" |
| 15:57:18 | 11.0 s | 25.7 | "!" |
| 17:21:21 – 17:31:13 | 13 events | 18.7–25.2 | the assistance case below |

### The finding that matters: a real Coast Guard assistance case, zero detections

Between 17:21 and 17:31 the rig captured a complete case end to end — vessel
hailing with no answer, Coast Guard response, GPS position requested, anchor
advised, vessel description and cell number taken, a landline attempt, TowBoatUS
("HOPO USA", "Hobo") brought in and confirmed visual, then "if your situation
changes or worsens… Coast Guard out."

**`detection` rows for the whole day: 0.**

Nothing in the watchword list was ever spoken. The list carries "adrift",
"aground", "taking on water", "require assistance"; what was actually said was
*"can I get a GPS position"*, *"do you have an anchor on board"*, *"are you able
to deploy it"*. **The emergency is legible only from the Coast Guard's
questions**, and the vessel's own replies are among the transmissions that did
not transcribe.

This is exactly the gap Tier 2 exists to close, and Tier 2 was **off all day** —
no `VHFWATCH_ANTHROPIC_API_KEY` set. So the session produced the best test case
this project has ever had: **replay 17:21–17:31 with the classifier enabled and
see whether it alerts.** That is a measured answer to "does Tier 2 earn its
place", against real traffic rather than a synthetic fixture.

### Small craft do not transcribe; the Coast Guard does

Every clean transcript today is a Coast Guard transmission. Every "!" is the
other side of a conversation. One clip (17:24:25) contains **both**: it opens
"! !" — the vessel — then switches to clearly-transcribed Coast Guard audio.
Same recording, same radio, same moment.

Signal strength does not separate them cleanly: 17:22:07 transcribed a full
sentence at 19.8 dB while 15:57:18 produced "!" at 25.7 dB with
`no_speech_prob` 0.05 — eleven seconds of audio the model was *confident* was
speech and could not read a word of. This is the 2026-09-07 finding
(`est_snr_db` does not measure intelligibility) reproduced with a much stronger
case, and it argues for the water-side relocation over any model change: a
bigger model cannot recover what the RF path destroyed.

**Vessel names are the consistent casualty** — one vessel appeared as "Sir
Muggis", "Sir budget", "For budget", "For budgets", "For much assistance", and
TowBoatUS as "Hobo" / "HOPO USA". In a real mayday the name is what identifies
the boat, and it is on the audio but not in the text.

### CAPTURE DIED SILENTLY AT ~17:31 AND NOTHING NOTICED FOR 50 MINUTES

The UFO202 vanished from the system — absent from `audio_devices.py` and from
the USB bus — and:

- the pipeline process **stayed alive and logged nothing**;
- `/health` would have looked normal;
- the noise-floor drift warning did **not** fire until 18:24, ~53 min later;
- it was caught only because the half-hourly idle check tries to **open the
  device directly** and got `ValueError: No input device matching 'USB Audio CODEC'`.

At 18:24 a stalled buffer flushed through and produced a bogus 1.8 s
"transmission" **stamped 17:36:33** — `started_at` is derived from sample
counting, so a stalled stream drifts behind wall clock — transcribed as
"No, I love, I..." on a line measuring **−57.5 dBFS** against −39.8 calibrated.
That row is annotated in the database as not-real-audio; exclude it from corpus,
labelling and `evaluate.py`.

**Cause not established.** Cable, hub or dock power are the candidates; the rig
was shut down before it could be diagnosed. Check it before trusting the next
session.

⚠️ **This is the failure mode AGENTS.md calls the worst outcome, observed for
real.** A quiet channel and a dead capture chain are indistinguishable from the
log. Concrete Phase 4 requirements this produces:

1. A **capture heartbeat**: assert that audio frames are still arriving, not
   merely that the process lives. Sample-count time falling behind wall clock is
   a direct, cheap signal.
2. The drift warning fires **once** and only when the estimate moves. It is not
   a substitute for (1).
3. Probing the device (opening it, not reading a cached list) is the check that
   actually worked today — the half-hourly idle reading should become part of
   the product, not a session habit.

### Pushover works, end to end, for the first time

`scripts/notify_transmissions.py` (new, a bring-up tool — NOT the alert path)
sent one push per transmission with transcript and a LAN audio link, 4–13 s
after each transmission ended. Setup notes:

- Credentials live in `~/.vhfwatch-pushover.env` (outside the repo, mode 600),
  sourced into the process environment. They were entered twice in the wrong
  slots and once with a corrupted character; `/1/users/validate.json` is the
  fastest way to tell "wrong key" from "wrong slot".
- The web app was temporarily bound to `0.0.0.0` with `base_url` set to the
  station LAN IP so links opened on the phone; **both reverted at shutdown.**
- Audio links reuse ONE share token minted against the most recent incident,
  because share-token scope is per-incident and there is no per-transmission
  scope. Test-harness compromise, documented in the script.
- **The tool pushed a transcript the pipeline had REJECTED** (`! ! ! ! ! ! !`,
  caught as a repeated token) without saying it was rejected. Fine for a
  bring-up aid, would be misleading in anything operational.

### Next session

- **Diagnose the USB dropout first.** Everything else is worthless if capture
  dies unobserved.
- Replay 17:21–17:31 with the classifier enabled. Best available answer to
  whether Tier 2 would catch a real case.
- Volume 12 still pending from 2026-09-15; two clips today at −0.4 dBFS.
- Water-side relocation is now the best-supported change: it is a new site, so
  re-run `calibrate.py` there (D11).

---

## 2026-10-01 — third rig scoped: MCS 2000 accessory tap, and Ch 16 confirmed on it

**Who:** Parker · **Rig:** NEW — Motorola MCS 2000 at the fire station
**Session:** research and identification only. **Nothing wired.**

A spare MCS 2000 (City of Bellingham asset 2168, remote head, external Motorola
speaker, Astron RS-35A linear supply on 115 VAC). The communications chief
authorised modifying **this** radio because it is a spare and out of service —
that authorisation does not extend to the other station radios.

Full pinout, tap point, hard rules and build procedure: **`docs/hardware.md`
§3.9.** Only what happened today is below.

### Ch 16 confirmed — the assumption everything rested on

**Zone `WDBAND`, channel `WCG16`. Marine traffic heard by ear.** Until this,
every pin number and parts order was speculative work on a radio that might not
have had Ch 16 in its codeplug at all.

### Connector identification cost most of the session

Three wrong guesses before the right one, all worth recording because the next
person will make the same ones:

1. **Not the rear panel.** The accessory connector is on the **underside**.
2. **Not the 18-pin connector on the side** — that is the control head, and its
   cable is labelled `TO CONTROL HEAD`. Unplugging it takes the radio down.
3. **Not the Astron supply**, which is the box the radio sits on and looks like
   part of the radio in photographs.

The connector is an **HLN6412A**, 25-pin, already fitted with the factory
jumpers (1–2 internal speaker, 4–9 emergency to digital ground) plus a red wire
on a bottom-row contact consistent with IGNITION. **Both spare-looking gold
contacts turned out to have wires**, so contacts have to be bought.

### The tap is pre-volume, which retires two standing problems

Pin 11 `FIL AUD OUT` sits before the volume control. The handheld rig's capture
level depends on the volume knob being at exactly 13 — held by tape and a note —
and 4 transmissions across three sessions have hit `clip_warn_dbfs`. Neither
applies to a pre-volume tap. Still squelch-gated, so the segmenter is unchanged.

### Parts ordered

| Part | Source | Note |
|---|---|---|
| TE **1658537-2** D-sub size 20 socket contacts ×6 | DigiKey, $0.29 ea, min qty 1 | The connector takes **standard D-sub sockets**, not a Motorola-only part — this is what made same-week shipping possible after eBay looked like the only route |
| RCA ground-loop isolator | Amazon, ~$10 | Not optional on this rig: radio and laptop are both earthed through mains |
| RCA cable, one end cut off | already owned | Centre → pin 11, shield → pin 10 |

### Next session

1. **Meter the plug's jumpers first** (one adjacent pair, one five apart in the
   same row) to prove the pin numbering on the physical connector. Counting from
   the wrong end lands on `MIC IN` and `EMERGENCY`.
2. Fit contacts to 10 and 11; **meter 11→10 for DC before the sound card goes
   anywhere near it.**
3. `level_meter.py`, `calibrate.py`, new thresholds. Nothing transfers from the
   handheld rig (D11).

---

## 2026-10-09 — MCS 2000 tap BUILT and working: 54 dB SNR, 50 dB quieter than the rig it replaces

**Who:** Parker · **Rig:** NEW — Motorola MCS 2000, fire station, pin 11 tap
**Session:** build, debug and calibration. Pipeline live on Ch 16 from 15:53.

### Result

| | MCS 2000 (pin 11) | Handheld rig it replaces |
|---|---|---|
| Idle floor, band-limited 20 ms | **−91.3 dBFS** | −39.8 |
| Loudest idle window | **−89.5** | −34.9 |
| Idle spread (median→max) | **1.8 dB** | 5.5 dB |
| Speech p50 / p99 | **−52.9 / −33.2** | −16.2 / — |
| First live transmission | **54.1 dB SNR, peak −13.5** | 18–29 dB, peaks at 0 (clipping) |
| Transcript | **"Radio check on channel 16. Radio check." — exact** | names mangled, 5 of 18 unreadable |

Config: `noise_floor_dbfs = -91.3`, `close_threshold_db = -87.5`,
`open_threshold_db = -83.5`, `site_name = "bellingham-station-mcs2000"`.
Handheld values backed up outside the repo and preserved in earlier entries.

**Volume knob is now irrelevant** — pin 11 is pre-volume. The taped-knob
dependency and the clipping headroom problem are both retired.

### Pin numbering: confirmed, not assumed

Lettering the plug from the wire side (latch right, top row A–M, bottom N–Y)
and metering for the factory jumpers gave **A–B** (adjacent) and **D–I** (five
apart) plus the red wire in **O**. That maps to jumpers **1–2** and **4–9** and
IGNITION on **15** — three independent landmarks, all consistent, orientation
fixed. **J = pin 10, K = pin 11.** The method worked exactly as written in
§3.9; keep it.

### What cost the session: contacts that look seated and are not

Hours went to a chain that read **dead in LINE mode** at every stage. The cause
was mechanical: the new contacts were in the right holes but **not pushed in
far enough to latch.** An unseated socket sits near its pin without touching,
couples capacitively, and passes a signal roughly **40 dB down** — which in
LINE mode is below the noise floor and looks exactly like nothing at all.
Reseating them fixed it completely and the level came up as designed.

**Both failure modes look identical through a sound card.** Distinguish them by
the idle floor with the cable connected: an unconnected input reads ≈−96 dBFS
(the converter's own noise), a connected one is noticeably higher.

### Two diagnostic techniques worth keeping

**1. PHONO mode as a 40 dB detector.** The UFO202's phono preamp adds ~35–40 dB
plus RIAA EQ. Flipping to PHONO turned an invisible signal into one clipping at
0 dBFS, which proved audio was present and reframed the problem from "no
signal" to "signal far too weak". The EQ makes it useless for real audio —
**diagnostic only, flip back to LINE** — but as a presence test it is excellent
and needs no equipment.

**2. Do NOT trust a DMM on AC volts at a floating connector.** Early on, a
meter at the unplugged RCA showed "a big jump" on monitor, which sent us
looking downstream for an hour. A high-impedance AC input picks up hand
capacitance and mains hum and will happily read volts on a dead line. The
honest test is a loaded one, or a sound card.

### calibrate.py was contaminated by channel surfing

A first run reported a **loudest idle window of −21.1 dBFS** against a −91.2
median — the operator had passed through an active channel while tuning back to
16. Thresholds derived from that tail would have been **−15.1 / −19.1**, above
most speech, and the gate would never have opened. Re-run on a genuinely idle
channel: tail −89.5, and the numbers in the table above.

**Sanity check to apply every time:** if the loudest idle window sits more than
~10 dB above the median, the run is contaminated. Here the final spread was
1.8 dB.

### 16:41 — the first EAR-CONFIRMED unintelligible transmission

Two transmissions at 16:41:43 (3.3 s) and 16:41:47 (1.9 s), at **SNR 58.7 and
57.8 dB — the cleanest signals this project has ever recorded.** Neither
transcribed:

| | 16:41:43 | 16:41:47 |
|---|---|---|
| MLX | `"!"` | `"Japan, go down."` — rejected, avg_logprob −1.21 |
| faster-whisper | `"This is your team."` ×4 (loop) | `"Capac, go south."` |

**The operator listened and could not read it either** — his read was a distant
station. That makes this the first case where an unreadable transcript is
confirmed to be unintelligible AUDIO rather than a model failure, and it
settles two things:

1. **A bigger model will not help this class of transmission.** The
   information is not in the recording. Do not spend the `medium.en`
   comparison on cases like this one.
2. **`est_snr_db` is now definitively disproven as a quality measure** — 58.7
   dB on audio no human can read, on a chain 50 dB quieter than the rig where
   the finding was first raised (2026-09-07). FM delivers full-level audio
   whether the station is next door or at the edge of range; only
   intelligibility degrades. Every remaining use of this metric should be read
   as "how far above the noise floor", never "how readable".

Both gates behaved correctly: MLX's guess was rejected on confidence, and
faster-whisper's loop is what `max_repeat_tokens` exists for.

### Open items

- **The ground-loop isolator is NOT in the chain.** It was removed during
  debugging and never put back; the current numbers are direct. Decide
  deliberately rather than by accident.
- **Calibrated on BATTERY.** The laptop was unplugged. Mains added 8.9 dB of
  hum on the old rig (D21); unknown here, and the tap is pre-volume and much
  quieter, so it may not matter. Re-measure before trusting a mains session.
- Contacts are seated in the HLN6412A housing, but the housing was in and out
  repeatedly today. Tug-test the wires before trusting a long run.
- Tier 2 and Pushover still unconfigured in this shell.

---

## 2026-10-09 (evening) — Telegram live; deployment host decided; two process lessons

**Who:** Parker · **Rig:** MCS 2000 station rig · **Live from 17:43 local** on Telegram

### Telegram replaced Pushover as the primary carrier (D26)

Distress alerts, silent health notices and **every transmission** now go to a
Telegram bot, each transmission as **one message: the recording with the
transcript as its caption**, playable inline. Reason: transcripts here are
wrong in exactly the places that matter (one vessel came through as five
different names in one incident), and the 16:41 pair proved that a clean 58 dB
transmission can be unintelligible to the ear too. See D26 for what is given up
(Pushover's emergency priority — acceptable only because this operator's phone
is never on Do Not Disturb).

`notify_every_transmission` is **off by default** and the example config says
MARINE ONLY. `config.toml` is gitignored, so the switch-over to Telegram lives
on the station machine only.

**Setting up the bot, and how it went wrong.** The token and chat ID were
entered into a hidden prompt in the wrong order, leaving the chat ID in both
slots. Checks that caught it without printing a secret: token shape
(`<digits>:<~35 chars>`), then Telegram's `getMe`, which returns the bot's
public name. The chat ID is best fetched, not hunted for: message the bot, then
call `getUpdates` and read the chat id from the reply. A bot cannot see a user
who has not written to it first, and a `getUpdates` result of zero usually means
that, or the wrong bot.

### Deployment host: x86 N100 mini PC, not a Raspberry Pi

Decided with the operator. Reasoning, so it is not re-argued: the docs already
name x86 Linux as the target; faster-whisper is the deployment engine and is
x86-optimised; price is the same (~$150 complete); no SD-card fragility; one
platform fewer to keep working. **Buy one with Intel wifi (AX101/AX201), not
Realtek RTL8821CE** — the station has wifi only, so the wireless chipset is
load-bearing. Ubuntu Server, headless. The UFO202 stays at the station.

**Power loss is acceptable by design.** The database is already WAL, so a cut
loses at most the last transaction and cannot corrupt the file. The requirement
is only that it comes back by itself — hence D24 (crash, let `systemd
Restart=always` restart) rather than in-process recovery. No UPS.

### Lesson: a stale process survived a "stop" — and the first diagnosis was wrong

Stopping the pipeline with `kill -INT` did nothing, and the old instance kept
running **alongside** the new one, both capturing the same device and writing the
same database. The stale one had its silence threshold forced to 0 and would have
notified every tick. Caught by listing processes, not by any alert.

**Corrected 2026-10-09 (same evening).** This entry first said the signal "hit a
wrapper shell, not the Python process". That was a guess and it was wrong. The
real cause, tested: a Python process started with `cmd &` or `nohup cmd &` from a
**non-interactive shell inherits SIGINT as IGNORED** (`signal.getsignal(SIGINT)`
returns `1`), so Python never installs its KeyboardInterrupt handler and
`kill -INT` does nothing at all. SIGTERM did work, but killed the process with no
cleanup (a leaked-semaphore warning gave it away).

**Fixed in code, not by habit:** `pipeline.run_until_signalled` now handles both
SIGINT and SIGTERM itself, so shutdown is clean however the process was launched,
and `systemctl stop` (SIGTERM) drains queued notifications rather than killing
mid-write. A regression test starts the pipeline with SIGINT ignored and checks
both signals. **After any restart: `pgrep -fl "vhfwatch.pipeline"` and confirm
exactly one.**

### Verified end to end

- Health notice via Pushover reached the phone with `no_audio_alert_hours`
  forced to 0 (the silence alert works).
- Telegram: text notice (silent) and an audio message with caption both arrived
  and played.
- A seven-clip demo set from 2026-09-23 was sent to the operator's own chat,
  for showing others.

### Verified live, later the same evening

After the outbox, daily check and signal-handling work (D28), the pipeline was
restarted on the new code on the station laptop. **The "started" notice and the
daily "alive" check both reached the operator's phone through Telegram, silently**
— the first time the outbox, the Telegram channel and the health path have been
proven together outside a test. A restart 40 seconds later correctly sent neither
(the 10-minute throttle and the once-per-day record both held), and SIGINT to the
background process now shuts it down cleanly in about a second.

**Then a live transmission went through it, too (19:12 local).** Three short
transmissions (2.6-3.1 s) at **61-62 dB SNR** — the strongest signals this
project has recorded — came through the station rig. The transcripts
(`"Sail, take some flight."`, `"Person, stop."`, `"16, sensor,"`) were all
**rejected on confidence** (average log-probability -1.2 to -1.8 against the
-1.0 limit), and each still reached the operator's phone through Telegram as a
recording with its transcript **labelled rejected**. The operator listened and
**could not understand the audio either.** So the gate was right, the labelling
was right, and the path worked end to end — and this is a second ear-confirmed
case that `est_snr_db` says nothing about intelligibility (see 16:41 above).

Still unobserved: any of this on the N100.

Gap found, then fixed the same evening: the outbox logged failures and
abandonments but **not successful deliveries**, so "no failure in the log" was
the only server-side evidence a message arrived, and confirmation had to come
from the operator's phone. It now logs `outbox.delivered` with the attempt count
and time queued; a delivery with `attempts` above 1 is a recovered wifi drop.

### Still not built, and blocking an unattended week

Daily self-test push; systemd units; per-stage heartbeats for ASR, detection and
alerting (only the capture heartbeat exists); **retry of a failed notification
across the station wifi drops** — a failed Telegram send is logged and lost.
The first live transmission through the Telegram path has not yet been observed.

---
