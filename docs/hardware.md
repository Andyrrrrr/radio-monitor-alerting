# Hardware setup and bring-up

POC hardware, the gotchas in it, and a verification procedure that tells you which link in the chain is broken before you go looking for bugs in software.

---

## 1. Two rigs, one codebase

Development happens on two different audio front-ends. **This is supported deliberately, but it has consequences.**

| | Andy's rig | Parker's rig |
|---|---|---|
| Radio | Kenwood NX-5200 | Kenwood NX-5200 |
| Adapter | multi-pin → K1 2-pin | multi-pin → K1 2-pin |
| Cable | 3.5 mm TRS → dual 1/4" **TS** | 3.5 mm TRS → dual **RCA** |
| Interface | **MOTU M2**, input **2**, 1/4" line | **Behringer UCA202**, RCA L/R |
| Capture channel (`use_channel`) | **1** (M2 in 1 = ch 0, in 2 = ch 1) | 0 |
| Input type | Balanced TRS (**cancellation risk**, §3.4) | Unbalanced RCA (no such risk) |
| Hardware input trim | Yes, per-channel knob | **No** — fixed line level |
| Metering | Front-panel LCD | None |
| Host | MacBook Pro (Apple Silicon) | MacBook Pro |

**Consequences that matter for code and process:**

1. **`config/config.toml` is per-machine and gitignored.** Device name, thresholds, and gain differ between rigs. Never commit it. `config/config.example.toml` is the shared template.
2. **Calibration values do not transfer between rigs.** Noise floor, `open_threshold_db`, and gain settings are properties of one specific radio + cable + interface + volume-knob position. Each person calibrates their own and records it in `docs/bringup-log.md` under their own heading.
3. **Corpus audio is rig-specific in level and noise character but still shareable and worth sharing.** A corpus recorded on the M2 rig is perfectly good for developing and testing the segmenter, ASR, and detector. It is *not* a substitute for calibrating thresholds on the other rig. Share corpus freely; re-derive thresholds locally.
4. **Test on both before trusting a segmenter change.** A threshold tweak that improves things on one rig can regress the other.

---

## 2. Radio

**The radio is already programmed with marine channels.** No programming software needed for the POC.

### 2.1 Spot-check the existing programming

A mismatch here produces symptoms that look exactly like software bugs, so verify once.

**You probably can't check these directly** — that needs the dealer programming software (§2.3), which is Windows-only and not freely available. **The by-ear listening test (§5 Step 1) verifies three of the four for free:** if you hear clear marine voice on Ch 16, then modulation, bandwidth, and tone squelch are all necessarily correct, because a wrong mode gives silence, narrow bandwidth gives quiet distorted audio, and any tone squelch mutes everything. **TX inhibit is the exception, and must not be tested by transmitting** — just never press PTT on the monitoring radio.

| Setting | Required value | Why |
|---|---|---|
| **Modulation** | **Analog FM** | The NX-5200 is P25/NXDN-capable. Marine VHF voice is analog FM. A digital mode gives silence or noise. |
| **Bandwidth** | **25 kHz (wide)** | Marine VHF uses 25 kHz spacing (16K0F3E). Narrow 12.5 kHz gives quiet, distorted audio that measurably degrades transcription. |
| **TX inhibit** | **Receive-only** | Prevents transmitting on marine channels with a land mobile radio — illegal, and potentially transmitting over live distress traffic. |
| **CTCSS/DCS** | **None** | Marine VHF is carrier squelch. Any tone squelch mutes everything. |

### 2.2 Channel

**Park on Channel 16 (156.800 MHz). Do not scan.** Scanning creates gaps, and a transmission beginning while the radio dwells elsewhere is partly or entirely lost. One radio, one channel, no gaps. Multi-channel monitoring needs multiple receivers or an SDR.

Other channels for later reference: 22A (157.100, USCG liaison — where traffic moves after initial contact), 09 (156.450), 13 (156.650), 12/14 (156.600/156.700), 68/69/71/72 (recreational working channels).

Channel 70 (156.525) is the DSC data channel. No voice, and this radio can't decode it. Skip it.

### 2.3 Reprogramming, if ever needed

Kenwood **KPG-D1N / KPG-D6N** — dealer-distributed, Windows-only, not freely downloadable. The FTDI programming cable in the parts list is what it connects to. That cable carries **serial data only and cannot carry audio** — see §3.1.

---

## 3. Audio chain

### 3.1 The programming cable is not an audio cable

The FTDI cable presents as a **USB serial port** (`/dev/tty.usbserial-*`). It carries serial data for reading and writing the radio's codeplug. It does not carry analog audio and cannot present as an audio input device. No configuration changes this. It is for programming only.

The **multi-pin → K1 2-pin adapter** is what gives you audio: a **3.5 mm speaker output** (K1 convention: 3.5 mm = speaker out, 2.5 mm = mic in).

**Verify your adapter in thirty seconds:** plug wired earphones into the 3.5 mm side, power the radio, open squelch. Hiss in the earphones means you have a working analog audio tap. That single test settles it. If it terminates in USB or DB9, it's a data cable regardless of the listing.

### 3.2 Parker's chain — UCA202

```
NX-5200
  └─ multi-pin accessory connector
       └─ [multi-pin → K1 2-pin adapter]
            └─ 3.5 mm TRS speaker out
                 └─ [3.5 mm TRS → dual RCA cable]
                      └─ Behringer UCA202, RCA inputs L/R
                           └─ USB → MacBook
                                └─ Core Audio, 48 kHz, 2-in
                                     └─ sounddevice, CHANNEL 0 ONLY
```

**Advantages of this rig:** RCA inputs are **unbalanced**, so the phase-cancellation trap in §3.4 does not apply. Inputs are genuine line level, so **no attenuator is needed.** Class compliant — no drivers.

**Limitation: no hardware input gain.** The UCA202's front knob controls *headphone output only* and has no effect on the recorded signal. See §3.5.

The UCA202 is 16-bit/48 kHz. Irrelevant here — marine VHF audio is ~3 kHz bandwidth and heavily noise-limited long before bit depth matters.

### 3.3 Andy's chain — MOTU M2

```
NX-5200 → [K1 adapter] → 3.5 mm TRS out
  └─ [3.5 mm TRS → dual 1/4" TS Y-cable, ONE leg used]
       └─ MOTU M2, input 2, 1/4" jack (line, NOT XLR)
            └─ USB-C → MacBook → Core Audio, 48 kHz, 2-in
                 └─ sounddevice, CHANNEL 1 ONLY (use_channel = 1)
```

**Input 2, not input 1, on this rig.** Input 1 carries Andy's XLR mic and stays where it is. The physical jack determines the capture channel: **M2 input 1 = channel 0, input 2 = channel 1.** Set `use_channel` to match, and set the trim on the strip the radio is actually plugged into — the M2's trims are per-channel.

Phantom power is only present on the XLR contacts of a combo jack, never the 1/4" contacts, so a phantom-powered condenser on input 1 cannot harm the radio connection on input 2.

M2 settings: **1/4" input, not XLR** (the XLR path is a mic preamp expecting millivolts and will be slammed even at minimum gain). Phantom power **off** on the radio's channel. Hi-Z/instrument **off**. 48 kHz in Audio MIDI Setup. Front-panel meters make level setting direct — use them.

Bonus: the M2's **loopback** lets you play a recorded distress call out of the Mac and back through the real audio input path for end-to-end testing without transmitting.

### 3.4 The balanced-input trap (M2 rig only)

**The M2's 1/4" line inputs are balanced and compute tip minus ring.** If the radio presents the same mono audio on both tip and ring — which many radio accessory outputs do — tip minus ring is **zero, and you get near-silence or a thin, weak signal.**

This looks exactly like a broken adapter or a radio that isn't receiving. It is the most likely failure on the M2 rig.

**Avoid it with an unbalanced mono connection:** use a **3.5 mm TRS → dual 1/4" TS** Y-cable and plug **one leg** into the radio's input (input 2 on Andy's rig). A TS plug shorts ring to sleeve, so the M2 sees tip minus ground = your signal. Do **not** use a 3.5 mm TRS → 1/4" TRS cable.

**This does not apply to the UCA202.** RCA is unbalanced by construction.

### 3.5 Gain structure and calibration

**On the UCA202 rig the radio's volume knob may be your only gain control.** The UCA202 has no input trim, and macOS may grey out the input volume slider for devices that don't expose software-controllable input gain. Check System Settings → Sound → Input; if the slider is adjustable, that's a usable second stage, if not, the radio knob is it.

**Confirmed 2026-08-12 on the M2:** macOS shows *no* input slider for it at all. That's fine on this rig — the hardware trim is the real gain stage — and it means there's no software slider to be reset by a reboot. Don't go looking for one.

For that case the pipeline provides a software gain stage:

```toml
[audio]
input_gain_db = 0.0   # post-capture digital gain
```

**Software gain can only fix "too quiet." It cannot fix clipping** — clipping is destructive and happens upstream of anything code can do. Always solve level problems in the analog domain first and treat `input_gain_db` as the last resort.

**Calibration procedure (both rigs):**

1. Select the interface as the Mac's input. Confirm the app sees it and reports 2 input channels: `python scripts/audio_devices.py`.
2. Set 48 kHz in Audio MIDI Setup.
3. Get a steady signal to set the trim against — **squelch open** (§3.6) for continuous hiss, or, if your radio has no squelch control, a **keyed transmission from a second radio on a channel you're licensed for** (§5 Step 4). Note these two conditions produce different targets: hiss at −45 to −35 dBFS, speech peaks at −12 to −6.
4. **Radio volume to roughly 1/3** and adjust from there. Radio speaker amps distort when driven hard, and distortion introduced at the radio cannot be undone downstream — it degrades transcription more than low level does. Keep the radio clean.

   ⚠️ **This ordering assumes the radio is the noisiest stage. Check that before applying it.** If your rig picks up interference on the *cable* — downstream of the radio's volume knob, as the M2 rig does (§3.4 territory, 60 Hz pickup on a muted output) — then the interface trim amplifies signal and interference equally, and the radio's volume is the *only* control that improves SNR. On such a rig the correct staging is the opposite: **radio as high as it goes without distorting, trim as low as will avoid clipping.** Moving gain from the radio to the interface cost the M2 rig **30 dB of noise floor** for 8 dB of signal (`docs/bringup-log.md` 2026-08-12, `docs/decisions.md` D18). Decide which stage dominates by measuring the idle floor at two different splits, not by assuming.
5. On the M2, bring up input 1 trim while watching the meters. On the UCA202, adjust the radio knob (and the macOS slider if available) while watching levels in `scripts/calibrate.py`.
6. Target: **open-squelch noise around −45 to −35 dBFS**, **speech peaks −12 to −6 dBFS**, never touching 0.
7. **Tape every knob you set.** Radio volume, and M2 trim if applicable. Bumping one degrades detection silently.
8. **Measure the noise floor with squelch CLOSED and nobody transmitting** (`scripts/calibrate.py`), and record it in `config.toml` so the VAD threshold derives from measurement, not a guess. Closed is the condition that matters: the gate's job is to clear whatever the line does when there's no signal. Squelch-open hiss is a trim-setting signal, not a floor. Also record the macOS input slider position in the bring-up log — it doesn't always survive reboots or device reconnects.

   `calibrate.py` measures **full-band** RMS, which will include any mains pickup on an idle line. That's correct for a gate threshold, and don't "fix" it with a filter without checking the hum's behaviour first — on the M2 rig the pickup vanishes entirely when the squelch opens (the radio's output stage drives the line and shorts it out), so it can never mask speech. See `docs/bringup-log.md` 2026-08-12.
9. Add a startup check that measures noise floor and warns on >6 dB drift from the calibrated value. Cheap, and it catches a bumped knob, a half-inserted plug, a dead battery, and a reset slider.

Clipping is the enemy. It destroys transcription accuracy far more than low level does.

**Comparing two rigs: use peak-to-floor, not the floor.** When one rig's noise
floor looks far worse than another's, the absolute numbers do not tell you
where the noise comes from, and reasoning from them will send you to the wrong
stage. Compare **dynamic range** — speech peak minus idle floor — and get both
**volume knob positions** before forming any hypothesis. The logic: noise
originating in the radio's own output amp scales with the volume setting, so a
rig with a *lower* knob and *worse* peak-to-floor has its noise entering
somewhere downstream of that knob (cable, interface, USB power, charger), and
no interface trim or `input_gain_db` can improve it — both scale signal and
noise together. This is the same argument as `docs/decisions.md` D18, and it
decided the Parker/UCA202 rig too: 17/31 on the knob against Andy's ≈23, and
29.1 dB of range against his 60.5 dB (`docs/bringup-log.md` 2026-09-07). Two
wrong guesses were made on that rig before anyone asked for the knob number.

**Isolating which box is noisy:** disconnect the radio and measure the
interface alone with nothing on its inputs. If the floor barely moves, the
interface or its USB power is the source; if it collapses, the noise arrives
from the radio, cable or charger side. One cable pull, thirty seconds, and it
halves the search space.

**Ground loops:** low risk. The handheld runs on battery and is electrically isolated. If you later use a mains-powered base station, add a 1:1 audio isolation transformer.

### 3.6 Squelch: the tradeoff you're choosing

With a physical radio you have **no access to RSSI**, so signal-strength gating is unavailable. Segmentation is audio-energy VAD, and the radio's hardware squelch is your carrier-detect proxy.

**Squelch closed (POC default):** between transmissions the output is near-digital-silence, so a simple RMS threshold segments almost perfectly. Costs: squelch clips the first syllable (mitigated by pre-roll, below), and **weak signals that don't break squelch are completely invisible** — which are exactly the distant transmissions you most want.

**Squelch open:** you hear everything including sub-squelch signals. Costs: constant hiss makes energy-based VAD much harder, hallucination risk on noise-only segments rises sharply, and you transcribe far more garbage.

**For the POC: squelch closed, tuned as loose as it goes without opening on noise.** Log every segment's RMS and SNR so this can be revisited with data. Test open-squelch later with a proper VAD (Silero) rather than an RMS gate.

**Pre-roll buffer is mandatory.** Keep a rolling ~300 ms ring buffer at all times and prepend it when the VAD triggers. Without it you lose the beginning of every transmission, and "Mayday" arriving as "ayday" is a detection you threw away.

### 3.7 Software gotcha: channel selection

Both interfaces present as **2-in devices**. Which channel carries the radio depends on the physical jack you used and on the radio's output wiring — it may be one channel only, or duplicated on both.

**Slice exactly one channel, chosen by config (`use_channel`). Never average or mix the two.** Mixing a live channel with a silent one costs 6 dB and will quietly invalidate every VAD threshold you calibrated. Open the stream with `channels=2` and slice `use_channel`.

Channel mapping, per rig:

| Rig | Physical input | `use_channel` |
|---|---|---|
| Andy / M2 | input 2 (input 1 = his XLR mic) | **1** |
| Parker / UCA202 | RCA left | **0** |

A wrong `use_channel` produces near-silence, which is indistinguishable by symptom from a dead adapter, a closed squelch, or the §3.4 phase-cancellation trap. Rule it out first — it's the cheapest of the four to check. §5 Step 4b (level drops when you close squelch) is what proves you're on the right channel.

### 3.8 Rejected: Bluetooth

Parker's radio has Bluetooth. It is not a viable audio path. Recorded here so it doesn't get revisited — see `docs/decisions.md` for the full reasoning. Summary: macOS cannot act as a Bluetooth audio sink, and headset audio profiles apply AGC, noise suppression, and VAD that would actively break the segmentation design.

---

## 4. The antenna problem — set expectations honestly

**A handheld with a rubber-duck antenna on a desk indoors will hear very little marine traffic.** VHF is line-of-sight and range scales with antenna height. A stock portable antenna at desk height inside a building may hear only strong nearby stations.

**This happened, on the first rig, on day one.** As of 2026-08-12 Andy's house cannot receive Ch 16 at all with the stock antenna indoors — only a rebroadcast fire channel comes in. Treat the warnings below as a description of the likely outcome, not a hypothetical (`docs/bringup-log.md` 2026-08-12).

- **Do not conclude the software is broken when the radio simply can't hear anything.** Verify reception by ear during a busy period. If you hear nothing with earphones, no code will help.
- **A strong local rebroadcast is a fine segmenter test and a poor sensitivity test.** Fully-quieting audio validates the VAD logic but tells you nothing about the weak distant signals of §3.6. Don't promote "segmentation works" to "we'd hear a mayday".
- **Free wins:** radio next to or outside a window, as high as possible, away from computers and switching power supplies.
- **Best $25 in the project:** the NX-5000 series uses an **SMA** antenna connector. A telescopic whip or mag-mount with an SMA adapter, in a window with a view of the water, dramatically outperforms the stock duck.
- If reception is poor where you're developing, **record the corpus somewhere with a better view of the water** even if that's not where the system will live. The corpus needs representative signal quality; you can relocate.

Long-term priority order: **antenna height and quality first, feedline second, receiver third.** No software or model change competes with getting the antenna up high.

---

## 5. Bring-up verification procedure

In order. Each step isolates one link. Don't skip ahead — the point is knowing *which* link is broken. Log results in `docs/bringup-log.md`.

**Step 1 — Radio hears traffic (no computer).**
Park on Ch 16, listen by speaker or earphone during a busy period — daytime, good weather, active harbor. Expect radio checks, hails, USCG broadcasts.
✅ You hear traffic. ❌ **Stop.** Fix programming, squelch, battery, or antenna before touching software.

**Step 2 — Adapter passes analog audio.**
Earphones into the adapter's 3.5 mm output, squelch open.
✅ Hiss in the earphones. ❌ Wrong adapter, wrong jack, or wrong pinout (§3.1).

**Step 3 — Mac sees the interface.**
`python scripts/audio_devices.py`. Expect the UCA202 or M2 listed with **2 input channels** at 48 kHz.
✅ Listed with 2 channels. ❌ Check USB cable, and System Settings → Sound → Input.

**Step 4 — Signal is present, on the channel you think, and not cancelling.**
Radio ~1/3 volume. Run **`scripts/level_meter.py`** — it updates live, so you can set the trim while watching the number, which `calibrate.py` (a fixed-length batch) can't support. M2: also watch the front-panel meters. Use `calibrate.py` afterwards, once the level is right, to derive thresholds.

**If you can't open the squelch**, a keyed transmission from a second radio is a *better* test signal than open-squelch hiss anyway — it's real modulated audio at real levels. **Put both radios on a channel you are licensed to transmit on, never Ch 16** (§2.1, §5 Step 8). An FM receiver's audio output level doesn't depend meaningfully on frequency, so the calibration transfers; return to Ch 16 afterwards **without touching the volume knob or the trim.**

Calibrate the trim against the *loudest* signal you can produce — a nearby radio at full quieting. Real traffic lands below it, and that's the headroom you want.
✅ **4a:** level responds to radio noise — roughly −45 to −35 dBFS, peak nowhere near 0.
✅ **4b:** level drops sharply when you close squelch. This is the step that proves `use_channel` matches the jack you plugged into; a level that doesn't move is a channel you aren't listening to.
❌ **Wrong `use_channel`** → near-silence regardless of gain (§3.7). Cheapest to check, so check it first.
❌ **Near-silence at high gain on the M2 rig → phase cancellation** (§3.4). Switch to a TS connection.
❌ Near-silence on the UCA202 rig → check cable seating and radio volume. Cancellation is not the cause here.

**Step 5 — Mac records real radio audio.**
Record 30 s squelch-open. Inspect the waveform in QuickTime or Audacity.
✅ Visible noise, not a flat line, not a clipped square wave.
❌ Both channels identical → fine, but confirm your code slices channel 0 rather than averaging (§3.7).
❌ Flat line → routing or gain. Redo §3.5.

**Step 6 — Segmentation fires on real transmissions.**
Squelch closed, run the segmenter 30 minutes during a busy period.
✅ Segment count roughly matches what you heard; boundaries land on whole transmissions; no single transmission split into fragments.
❌ Tune `open_threshold_db`, `hang_ms`, `min_duration_ms`.

**Step 7 — Transcription is recognizable.**
Run ASR over Step 6's segments and read them against the audio.
✅ Imperfect but recognizable — vessel names garbled, gist intact.
❌ Check bandwidth programming (§2.1), levels, clipping.

**Step 8 — End-to-end alert.**
Replay recorded audio through the file source, or use the M2's loopback. **Never transmit a test mayday. Never transmit on Ch 16.**
✅ Notification arrives with a working link to an incident record containing playable audio and a transcript.

---

## 6. You are not blocked on hardware

**Phases 0 and 1 need no audio hardware at all.** The audio source is an interface with a file backend (`docs/architecture.md` §5.1), so the segmenter, ASR, detector, correlator, and store all build and test against WAV files.

- **Parker can develop against Andy's corpus** while parts ship. Recording rig differs; the code doesn't care.
- If you want real audio before the interface arrives, the **Mac's built-in mic pointed at the radio speaker** exercises the whole live path. Caveat: macOS applies voice processing (AGC, noise suppression) to built-in mic capture in some modes — the same problems that rule out Bluetooth. Use it for building and testing, **never for calibrating thresholds.** Any numbers derived from built-in-mic audio must be redone once the wire is in.
- **Start `scripts/record_corpus.py` as soon as any input works.** The corpus accumulates in wall-clock time and gates all of Phase 1's accuracy work. It's the one thing you cannot hurry later.

---

## 7. Long-term hardware

If the POC proves out, in value order:

1. **Externally mounted antenna, as high as possible**, low-loss coax, lightning arrestor, proper grounding. Dominates everything else.
2. **Dedicated fixed-mount receiver** parked on 16 permanently, so nobody retunes the operational radio.
3. **Shore/station install on mains power with a UPS.** A dead handheld battery is a silent failure.
4. **x86 mini PC** (Intel N100-class) as permanent host. The code must run there — see `docs/conventions.md` §5.
5. **Second receiver on 22A**, where USCG moves traffic after initial contact.
6. **SDR** — whole marine band at once, RSSI-based gating, plus DSC and AIS as supplements. Voice must still stand alone.
