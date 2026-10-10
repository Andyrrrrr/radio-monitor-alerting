# Deploying to the station host (Ubuntu Server 24.04, x86)

Runbook for the unattended week-long run, written 2026-10-09 against the
decisions in `docs/decisions.md` D24 (crash and restart), D26 (Telegram) and
D27 (the N100 host).

> **Status: written on a Mac, not yet run on real hardware.** Every command
> below is from documentation, not from a session on the target. Expect to fix
> something on first boot, and write what you fix back into this file — that is
> the point of having it.

The design in one paragraph: the pipeline **exits non-zero** whenever capture
stalls or a stage hangs (D24), and **systemd restarts it**. There is no
in-process recovery to half-work. Power loss is acceptable by design: the
database is WAL, so a cut loses at most the last transaction, and
`Restart=always` plus `WantedBy=multi-user.target` brings everything back on its
own. A restart sends one silent "started" notice, which after a power cut is the
only sign it recovered.

## 0. Before you buy or boot

**Check the wifi chip.** The station has wifi only, so this is load-bearing.

```bash
lspci -nn | grep -i network
```

- **Intel** (AX101 / AX201): keep it. AX101 needs kernel 6.4+; Ubuntu 24.04
  ships 6.8, so this works. **Do not use 22.04.**
- **Realtek**: return it.

**If the host is a refurbished HP ProDesk 600 G3 Mini** (or the similar
EliteDesk 800 G3): the wifi is an optional factory part, and refurbishers
sometimes fit their own. It is a single M.2 2230 card held by one screw with two
antenna leads, so a Realtek card can be swapped for an Intel 8265 for roughly
$10-15. Check that the antenna leads are actually connected, or the signal will be
poor. Details and the questions to ask a seller are in `docs/decisions.md` (D27
addendum).

## 1. System packages and user

```bash
sudo apt update
sudo apt install -y ffmpeg libportaudio2 alsa-utils python3-venv git

# A dedicated, login-less user that may use the audio device.
sudo useradd --system --no-create-home --home-dir /opt/vhfwatch \
     --shell /usr/sbin/nologin --groups audio vhfwatch
sudo mkdir -p /opt/vhfwatch /etc/vhfwatch
sudo chown vhfwatch:vhfwatch /opt/vhfwatch
```

## 2. Code and virtualenv

```bash
sudo -u vhfwatch git clone https://github.com/Andyrrrrr/radio-monitor-alerting.git /opt/vhfwatch
cd /opt/vhfwatch
sudo -u vhfwatch git checkout parker-hardware-setup   # or main, once merged
sudo -u vhfwatch python3 -m venv .venv
sudo -u vhfwatch .venv/bin/pip install -e ".[linux]"
```

`[linux]` pulls `faster-whisper`, the deployment ASR engine (D25). The first
run downloads the model (~500 MB) into `HF_HOME`, so the host needs internet
that one time.

## 3. Config

`config/config.toml` is gitignored and per-machine — copy it from the laptop:

```bash
scp config/config.toml you@station-host:/tmp/config.toml
sudo install -o vhfwatch -g vhfwatch -m 640 /tmp/config.toml /opt/vhfwatch/config/config.toml
```

Then change exactly three things for Linux, and nothing else:

| Key | Mac value | Linux value | Why |
|---|---|---|---|
| `[asr] engine` | `"mlx"` | `"faster_whisper"` | MLX is Apple-Silicon only |
| `[audio] device` | `"USB Audio CODEC"` | same substring | ALSA names differ in detail; confirm in step 4 |
| `[general] site_name` | as is | as is | |

`[asr] model = "small.en"`, `fw_compute_type = "int8"` are already the right
CPU settings.

## 4. The audio device, and the ALSA gotcha

```bash
arecord -l                                              # the UFO202 should be listed
sudo -u vhfwatch .venv/bin/python scripts/audio_devices.py
```

**macOS has no capture-gain slider for this interface; ALSA does.** The mixer
may come up at a different level than the machine you calibrated on, which
would silently move the noise floor and every threshold with it.

```bash
amixer -c CODEC scontrols        # find the capture control
alsamixer -c CODEC               # F4 = capture; note the level
```

After setup, **re-run calibration and compare**:

```bash
sudo -u vhfwatch .venv/bin/python scripts/calibrate.py --seconds 60 --config config/config.toml
```

The MCS 2000 rig measured **−91.3 dBFS** on 2026-10-09. A floor within ~2 dB of
that means the chain is equivalent and the thresholds carry over. A bigger
difference means the capture gain differs — fix it in `alsamixer`, do not just
paste new thresholds over a changed chain.

**The UFO202's LINE/PHONO switch must be on LINE** (`docs/hardware.md` §3.2).

## 5. Secrets

```bash
sudo install -o root -g vhfwatch -m 640 deploy/vhfwatch.env.example /etc/vhfwatch/vhfwatch.env
sudo nano /etc/vhfwatch/vhfwatch.env     # fill in the Telegram token and chat id
```

Plain `KEY=value`, no `export`, no quotes. Never commit the real file.

## 6. Run it once by hand before trusting systemd

```bash
sudo -u vhfwatch bash -c 'set -a; . /etc/vhfwatch/vhfwatch.env; set +a; \
  .venv/bin/python -u -m vhfwatch.pipeline --config config/config.toml'
```

Expect, in order: `asr.warmed_up`, `audio.device_selected`,
`audio.capture_started`, and a **"vhf-watch: started"** message in Telegram. Key
a radio nearby and confirm a transmission arrives with its recording. Ctrl-C.

If this works by hand and fails under systemd, the difference is environment —
the unit file or the env file — not the software.

## 7. Install the units

```bash
sudo cp deploy/systemd/vhfwatch-pipeline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vhfwatch-pipeline
systemctl status vhfwatch-pipeline
journalctl -u vhfwatch-pipeline -f
```

The web app is **optional** — Telegram carries the recording and transcript and
needs no web server. Install `vhfwatch-web.service` only for the browsable
incident records, and only over Tailscale or the LAN, never the open internet.

## 8. Prove the recovery paths (this is the real acceptance test)

Do each one deliberately, once, before leaving it unattended:

| Do this | Expect |
|---|---|
| **Unplug the USB interface** | Within ~30 s the log shows `audio.capture_stalled`, the process exits, systemd restarts it, and a "started" notice says the previous run ended after a capture stall |
| **Replug it** | Capture resumes by itself |
| **`sudo reboot`** | Everything returns with no keyboard; a "started" notice arrives |
| **Pull the power** | Same. Check `sqlite3 data/vhfwatch.db "PRAGMA integrity_check;"` says `ok` |
| **Disable wifi for 5 min, then restore** | Listening continues; on recovery one "notifications recovered" notice says how many arrived late |
| **Turn the radio off** | After `no_audio_alert_hours` (6) a "nothing heard" notice names the radio, not the computer |

If any of these does not behave as written, that is a bug in the system, not in
the test — fix it before the week starts.

## Failure modes

| Symptom | Probable cause |
|---|---|
| `No input device matching 'USB Audio CODEC'` | Not in the `audio` group, device not plugged in, or name substring differs — `scripts/audio_devices.py` |
| Restarts every ~30 s forever | Capture is genuinely dead; the watchdog is doing its job. Check the cable and `arecord -l` |
| `audio.noise_floor_drift` after install | Capture gain differs from the calibrated machine — `alsamixer` (step 4) |
| No Telegram messages, no errors | Env file not read: `systemctl show vhfwatch-pipeline -p EnvironmentFiles`; plain `KEY=value`, no `export` |
| `alert.telegram_failed … 401` | Wrong token. Not retried, by design — a bad token fails identically forever |
| `alert.telegram_failed … 400` | Wrong chat id, or the bot was never messaged (it cannot message you first) |
| One "daily check" every day at `self_test_hour`, none on a quiet day | Working as designed; the check is the proof of life |

## Updating

```bash
cd /opt/vhfwatch
sudo -u vhfwatch git pull
sudo -u vhfwatch .venv/bin/pip install -e ".[linux]"
sudo systemctl restart vhfwatch-pipeline
pgrep -fc 'vhfwatch.pipeline'      # must print 1
```

## Remote access

Install **Tailscale** (see tailscale.com/download/linux) on the host and on your
laptop; then `ssh` works from anywhere by name, with no port forwarding and
nothing exposed. Read an install script before piping it to a shell.

## Not built, so do not assume

- **`WatchdogSec=` / `sd_notify`.** systemd can also kill a *hung-but-alive*
  process. We rely on the pipeline's own watchdog (capture + stage stalls)
  instead; a hang in the watchdog task itself would not be caught.
- **Hardening** (`ProtectSystem=strict` etc.), deferred until the service is
  proven, for the reason in the unit file.
- **Recording retention.** `[storage] retention_days = 90` exists in the config,
  but **nothing deletes old recordings — the setting is not implemented.** At a
  few transmissions a day that is megabytes per week, so it does not matter for
  the trial, and the disk-low alert will warn long before it does. It needs a
  deliberate design before a months-long run, because deleting audio sits
  uneasily with "original audio is always retained and reachable from an alert"
  (AGENTS.md) — it should never delete audio belonging to an incident.
- **Log rotation** is handled by journald.
