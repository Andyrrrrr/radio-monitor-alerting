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
| Noise floor, squelch open (dBFS) | |
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
