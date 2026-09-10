"""RMS-gate segmenter: turns a stream of AudioFrames into Transmissions.

The core engineering problem of the project (docs/architecture.md §5.2).
Design decisions that are load-bearing, not style:

- **Pre-roll ring buffer.** Squelch clips the first syllable; without the
  ring buffer "Mayday" arrives as "ayday" and the detection is gone
  (docs/decisions.md D5).
- **Hysteresis + hang time.** The close threshold sits below the open
  threshold, and closing requires `hang_ms` of continuous quiet — together
  they stop "Mayday, mayday, mayday — this is vessel Serenity" becoming
  five unusable fragments.
- **Every discard is logged with its reason.** A rejected segment that was
  a real call is exactly what corpus review must be able to surface.

The class is deliberately synchronous and pure-ish (frames in, transmissions
out, no I/O): that's what makes the four required fixture tests possible
with no hardware and full determinism (docs/conventions.md §6).
"""

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt
import structlog

from vhfwatch.config import SegmenterConfig
from vhfwatch.models import AudioFrame, Transmission, new_id

logger = structlog.get_logger(__name__)

# Genuine constant, not a tunable: the RMS floor that stands in for digital
# silence so log10 never sees zero. -200 dBFS is far below any real signal.
_MIN_RMS = 1e-10


def _dbfs(rms: float) -> float:
    return 20.0 * math.log10(max(rms, _MIN_RMS))


class BandGate:
    """Per-window RMS restricted to a frequency band, for the GATE only.

    Why this exists: sub-300 Hz mains hum can sit ABOVE `open_threshold_db`
    while being completely unable to mask speech — it collapses ~28 dB the
    instant the squelch opens, because the radio's output stage then drives
    the line at low impedance (docs/decisions.md D17). Measured on Parker's
    rig, a mains-powered laptop put the idle floor at -31.8 dBFS against a
    -34.1 threshold, which would hold the gate open forever (D21). A marine
    VHF channel carries voice in roughly 300-3000 Hz, so energy outside that
    band is never signal and excluding it costs nothing real: measured 0.5 dB
    on speech against 7.4 dB of hum removed (D22).

    The ARCHIVED audio is never touched. It is evidence (AGENTS.md), and a
    human listening back must hear what the radio actually produced.
    """

    def __init__(
        self, low_hz: float, high_hz: float, sample_rate: int, window_len: int
    ) -> None:
        self._window = np.hanning(window_len).astype(np.float32)
        freqs = np.fft.rfftfreq(window_len, 1.0 / sample_rate)
        self._mask = (freqs >= low_hz) & (freqs < high_hz)
        self._scale = window_len / 2.0
        # Hann windowing removes power; dividing it back out keeps a
        # full-band reading on the same scale as a plain RMS, so the two
        # gate modes stay numerically comparable.
        self._correction = float(
            np.sqrt(np.sum(self._window.astype(np.float64) ** 2) / window_len)
        )

    def rms(self, pcm: npt.NDArray[np.float32]) -> float:
        spectrum = np.fft.rfft(pcm * self._window) / self._scale / self._correction
        power = float(np.sum(np.abs(spectrum[self._mask]) ** 2) / 2.0)
        return math.sqrt(max(power, 0.0))


@dataclass
class _Window:
    """One analysis window (frame_ms of samples) with its level."""

    pcm: npt.NDArray[np.float32]
    started_at: datetime
    rms_db: float


class Segmenter:
    """Feed frames with push(), collect Transmissions; flush() at stream end.

    State machine: CLOSED → (open_frames consecutive windows above
    open_threshold_db) → OPEN → (hang_ms below close_threshold_db, or
    max_duration_ms exceeded) → CLOSED, emitting a Transmission if the
    voiced span beat min_duration_ms.
    """

    def __init__(
        self,
        cfg: SegmenterConfig,
        sample_rate: int,
        channel: str,
        source_name: str,
    ) -> None:
        self._cfg = cfg
        self._rate = sample_rate
        self._channel = channel
        self._source_name = source_name
        self._frame_len = sample_rate * cfg.frame_ms // 1000

        # Gate measurement: full-band RMS by default, or band-limited when
        # configured. Affects the GATE and the noise-floor estimate only.
        self._band_gate: BandGate | None = (
            BandGate(
                cfg.gate_band_hz[0], cfg.gate_band_hz[1], sample_rate, self._frame_len
            )
            if cfg.gate_band_hz
            else None
        )

        # Ring holds the pre-roll PLUS the open-run windows, so that on open
        # we can take everything in one go: [preroll silence][open run].
        preroll_windows = math.ceil(cfg.preroll_ms / cfg.frame_ms)
        self._ring: deque[_Window] = deque(maxlen=preroll_windows + cfg.open_frames)
        self._consec_above = 0

        # OPEN-state accumulation. None means CLOSED.
        self._segment: list[_Window] | None = None
        self._n_preroll = 0  # windows in _segment that came from the ring's pre-roll
        self._last_voiced = (
            0  # index into _segment of last window above close threshold
        )

        # Running noise-floor estimate from closed-squelch quiet, for
        # est_snr_db. None until we've seen at least one quiet window.
        self._noise_floor_db: float | None = None

        # Incoming frames re-buffered into exact frame_ms windows. Timestamps
        # are anchored to the captured_at of the frame that started the
        # current run, plus sample offsets — so they self-correct across any
        # gap in the input rather than drifting forever.
        self._pending: deque[npt.NDArray[np.float32]] = deque()
        self._pending_samples = 0
        self._base_time: datetime | None = None
        self._consumed = 0  # samples consumed since _base_time

        # Visible drop accounting — the watchdog and tests both read this.
        self.discarded = 0

    # -- public API ------------------------------------------------------

    def push(self, frame: AudioFrame) -> list[Transmission]:
        """Process one frame; returns 0..n completed Transmissions."""
        if frame.sample_rate != self._rate:
            raise ValueError(
                f"segmenter configured for {self._rate} Hz, "
                f"got a {frame.sample_rate} Hz frame from {frame.source_name}"
            )
        if frame.pcm.ndim != 1:
            raise ValueError(
                "AudioFrame.pcm must be mono (1-D); slice channels upstream"
            )

        if self._pending_samples == 0:
            self._base_time = frame.captured_at
            self._consumed = 0
        self._pending.append(frame.pcm)
        self._pending_samples += len(frame.pcm)

        out: list[Transmission] = []
        while self._pending_samples >= self._frame_len:
            pcm = self._take_window()
            assert self._base_time is not None
            t = self._base_time + timedelta(seconds=self._consumed / self._rate)
            self._consumed += self._frame_len
            gate_rms = (
                self._band_gate.rms(pcm)
                if self._band_gate is not None
                else float(np.sqrt(np.mean(pcm**2)))
            )
            tx = self._process(_Window(pcm, t, _dbfs(gate_rms)))
            if tx is not None:
                out.append(tx)
        return out

    def flush(self) -> Transmission | None:
        """Close out an in-progress transmission at end of stream."""
        if self._segment is None:
            return None
        return self._emit(reason="end_of_stream")

    @property
    def noise_floor_db(self) -> float | None:
        """Current running noise-floor estimate (for drift monitoring)."""
        return self._noise_floor_db

    # -- internals ---------------------------------------------------------

    def _take_window(self) -> npt.NDArray[np.float32]:
        parts: list[npt.NDArray[np.float32]] = []
        needed = self._frame_len
        while needed > 0:
            block = self._pending.popleft()
            if len(block) <= needed:
                parts.append(block)
                needed -= len(block)
            else:
                parts.append(block[:needed])
                self._pending.appendleft(block[needed:])
                needed = 0
        self._pending_samples -= self._frame_len
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def _process(self, w: _Window) -> Transmission | None:
        if self._segment is None:
            self._process_closed(w)
            return None
        return self._process_open(w)

    def _process_closed(self, w: _Window) -> None:
        self._ring.append(w)
        if w.rms_db >= self._cfg.open_threshold_db:
            self._consec_above += 1
            if self._consec_above >= self._cfg.open_frames:
                # Open: take the whole ring — pre-roll quiet plus the open
                # run itself — as the start of the transmission.
                self._segment = list(self._ring)
                self._n_preroll = len(self._segment) - self._cfg.open_frames
                self._last_voiced = len(self._segment) - 1
                self._ring.clear()
                self._consec_above = 0
                logger.debug(
                    "segment.opened",
                    at=self._segment[self._n_preroll].started_at.isoformat(),
                    preroll_ms=self._n_preroll * self._cfg.frame_ms,
                )
        else:
            self._consec_above = 0
            if w.rms_db < self._cfg.close_threshold_db:
                # Only genuinely quiet windows feed the noise estimate, so
                # sub-open-threshold hum doesn't drag it upward.
                a = self._cfg.noise_ema_alpha
                if self._noise_floor_db is None:
                    self._noise_floor_db = w.rms_db
                else:
                    self._noise_floor_db = a * w.rms_db + (1 - a) * self._noise_floor_db

    def _process_open(self, w: _Window) -> Transmission | None:
        assert self._segment is not None
        self._segment.append(w)
        idx = len(self._segment) - 1
        if w.rms_db >= self._cfg.close_threshold_db:
            self._last_voiced = idx

        hang_elapsed_ms = (idx - self._last_voiced) * self._cfg.frame_ms
        if hang_elapsed_ms >= self._cfg.hang_ms:
            return self._emit(reason="hang_time")

        open_ms = (len(self._segment) - self._n_preroll) * self._cfg.frame_ms
        if open_ms >= self._cfg.max_duration_ms:
            # A stuck transmitter (or open squelch) would otherwise
            # accumulate forever. Emit what we have; the gate will simply
            # re-open on the next window if the carrier is still there.
            logger.warning(
                "segment.force_closed",
                open_ms=open_ms,
                max_duration_ms=self._cfg.max_duration_ms,
            )
            return self._emit(reason="max_duration")
        return None

    def _emit(self, reason: str) -> Transmission | None:
        assert self._segment is not None
        windows = self._segment
        self._segment = None
        self._ring.clear()
        self._consec_above = 0

        frame_ms = self._cfg.frame_ms
        voiced_ms = (self._last_voiced - self._n_preroll + 1) * frame_ms
        started_at = windows[0].started_at
        if voiced_ms < self._cfg.min_duration_ms:
            # Too short to be speech — a squelch crash or carrier tap.
            # Logged because a discard that was a real call is exactly what
            # corpus review needs to be able to find.
            self.discarded += 1
            logger.info(
                "segment.discarded",
                reason="below_min_duration",
                voiced_ms=voiced_ms,
                min_duration_ms=self._cfg.min_duration_ms,
                started_at=started_at.isoformat(),
                close_reason=reason,
            )
            return None

        pcm = np.concatenate([w.pcm for w in windows])
        ended_at = windows[-1].started_at + timedelta(milliseconds=frame_ms)

        # Level stats over the voiced span only — including the pre-roll and
        # hang-time quiet would understate every reading.
        #
        # rms_dbfs is the power mean of the per-window GATE levels, not a
        # fresh reading off the raw pcm. With full-band gating the two are
        # identical; with band gating they are not, and est_snr_db below
        # subtracts a noise floor built from those same gate levels — mixing
        # the domains would silently inflate the SNR of every transmission on
        # a hummy rig, which is exactly where it must not be optimistic.
        voiced_windows = windows[self._n_preroll : self._last_voiced + 1]
        mean_power = float(np.mean([10.0 ** (w.rms_db / 10.0) for w in voiced_windows]))
        rms_dbfs = 10.0 * math.log10(max(mean_power, _MIN_RMS**2))
        # Peak stays on the RAW audio: clipping happens at the ADC, and a
        # band-limited reading could hide it entirely.
        peak_dbfs = _dbfs(float(np.max(np.abs(pcm))))
        est_snr_db = (
            rms_dbfs - self._noise_floor_db
            if self._noise_floor_db is not None
            else None
        )

        if peak_dbfs > self._cfg.clip_warn_dbfs:
            # Clipping is destructive and upstream of us — the fix is an
            # analog knob, not code (docs/hardware.md §3.5).
            logger.warning(
                "segment.clipping",
                peak_dbfs=round(peak_dbfs, 1),
                clip_warn_dbfs=self._cfg.clip_warn_dbfs,
            )

        tx = Transmission(
            id=new_id(),
            channel=self._channel,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=round((ended_at - started_at).total_seconds() * 1000),
            pcm=pcm,
            sample_rate=self._rate,
            rms_dbfs=rms_dbfs,
            peak_dbfs=peak_dbfs,
            est_snr_db=est_snr_db,
            audio_path=None,
            audio_sha256=None,
            source=self._source_name,
        )
        logger.info(
            "segment.closed",
            transmission_id=tx.id,
            duration_ms=tx.duration_ms,
            voiced_ms=voiced_ms,
            rms_dbfs=round(rms_dbfs, 1),
            est_snr_db=round(est_snr_db, 1) if est_snr_db is not None else None,
            close_reason=reason,
        )
        return tx
