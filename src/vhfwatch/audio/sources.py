"""AudioSource implementations: File, Directory, and Live.

The Protocol is the seam that makes offline development possible: the
pipeline runs identically over a recorded WAV and a live radio
(docs/architecture.md §5.1).
"""

import asyncio
import wave
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import structlog

from vhfwatch.config import AudioConfig
from vhfwatch.models import AudioFrame

logger = structlog.get_logger(__name__)

# How often the non-realtime file reader yields to the event loop. Purely
# cooperative scheduling, not a tunable — nothing about a rig or site
# changes it.
_YIELD_EVERY_BLOCKS = 64


def resolve_input_device(name: str) -> int | None:
    """Substring-match a configured device name; "" means system default.

    Shared by LiveAudioSource and the calibrate/record_corpus scripts so
    they all pick the same device from the same config value.
    """
    import sounddevice as sd

    if not name:
        return None
    needle = name.lower()
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] >= 1 and needle in dev["name"].lower():
            logger.info("audio.device_selected", index=index, name=dev["name"])
            return index
    raise RuntimeError(
        f"no input device matching {name!r} — run "
        "scripts/audio_devices.py to list devices"
    )


class AudioSource(Protocol):
    name: str
    sample_rate: int

    def frames(self) -> AsyncIterator[AudioFrame]: ...

    async def close(self) -> None: ...


class FileAudioSource:
    """Reads a 16-bit PCM WAV and yields it as AudioFrames.

    `realtime=False` (the default) runs as fast as possible — that's what
    makes tests deterministic and corpus runs quick. `realtime=True` paces
    to roughly wall clock for watching a replay behave like live input.

    Only 16-bit PCM is supported for now: it's what the corpus recorder and
    the synthetic fixtures produce, and it keeps us on the stdlib `wave`
    module. If other formats show up, that's the moment to add `soundfile`
    (Phase 2, alongside live capture) — not to hand-roll 24-bit unpacking.

    `channel` is ignored for mono files. Corpus WAVs are written mono with
    the capture channel already sliced, so a rig configured with
    `use_channel = 1` would otherwise be unable to replay its own corpus.
    """

    def __init__(
        self,
        path: Path | str,
        blocksize: int = 1024,
        channel: int = 0,
        realtime: bool = False,
        start_at: datetime | None = None,
    ) -> None:
        self._path = Path(path)
        self._blocksize = blocksize
        self._channel = channel
        self._realtime = realtime
        self._start_at = start_at
        # Not a context manager on purpose: the reader must stay open across
        # the whole async iteration; close() is the lifecycle.
        self._wav = wave.open(str(self._path), "rb")  # noqa: SIM115
        if self._wav.getsampwidth() != 2:
            raise ValueError(
                f"{self._path}: only 16-bit PCM WAV is supported "
                f"(got sample width {self._wav.getsampwidth()} bytes); "
                "re-export the file or wait for soundfile support in Phase 2"
            )
        self._channels = self._wav.getnchannels()
        if self._channels == 1 and channel != 0:
            # `use_channel` describes the capture DEVICE's channel layout,
            # not a file's. record_corpus.py already applied the slice when
            # it wrote the file, so a mono corpus recorded on a rig with
            # use_channel = 1 (e.g. a radio on MOTU M2 input 2) must not be
            # asked for channel 1 again here — there is only one channel to
            # read. Log it rather than failing: refusing would make a rig's
            # own corpus unreplayable on the rig that recorded it.
            logger.info(
                "file.mono_ignoring_channel",
                file=self._path.name,
                requested_channel=channel,
                reason="file is mono; use_channel applies to live capture only",
            )
            channel = 0
        elif channel >= self._channels:
            raise ValueError(
                f"{self._path}: channel {channel} requested but file has "
                f"{self._channels} channel(s)"
            )
        self._channel = channel
        self.name = f"file:{self._path.name}"
        self.sample_rate = self._wav.getframerate()

    async def frames(self) -> AsyncIterator[AudioFrame]:
        # captured_at is the time of the frame's FIRST sample; downstream
        # timestamps are derived from it plus sample offsets.
        t = self._start_at if self._start_at is not None else datetime.now(UTC)
        blocks = 0
        while True:
            raw = self._wav.readframes(self._blocksize)
            if not raw:
                return
            interleaved = np.frombuffer(raw, dtype=np.int16)
            deinterleaved = interleaved.reshape(-1, self._channels)
            # Slice one channel, never average — mixing a live channel with
            # a silent one costs 6 dB and invalidates VAD calibration
            # (docs/hardware.md §3.7).
            pcm: npt.NDArray[np.float32] = (
                deinterleaved[:, self._channel].astype(np.float32) / 32768.0
            )
            yield AudioFrame(
                pcm=pcm,
                sample_rate=self.sample_rate,
                captured_at=t,
                source_name=self.name,
            )
            block_s = len(pcm) / self.sample_rate
            t += timedelta(seconds=block_s)
            blocks += 1
            if self._realtime:
                await asyncio.sleep(block_s)
            elif blocks % _YIELD_EVERY_BLOCKS == 0:
                await asyncio.sleep(0)  # stay cooperative on long files

    async def close(self) -> None:
        self._wav.close()


class DirectoryAudioSource:
    """Plays a corpus directory of WAVs in filename order — the tuning
    workhorse (docs/architecture.md §5.1).

    record_corpus.py names files by their start timestamp, so filename
    order IS timestamp order. Every file must share one sample rate; a
    mixed-rate corpus is a recording-setup bug worth failing loudly on.
    """

    def __init__(
        self, directory: Path | str, blocksize: int = 1024, channel: int = 0
    ) -> None:
        self._dir = Path(directory)
        self._blocksize = blocksize
        self._channel = channel
        self._paths = sorted(self._dir.glob("*.wav"))
        if not self._paths:
            raise FileNotFoundError(f"no .wav files in {self._dir}")
        first = FileAudioSource(self._paths[0], blocksize, channel)
        self.sample_rate = first.sample_rate
        self._first: FileAudioSource | None = first
        self.name = f"dir:{self._dir.name}"

    async def frames(self) -> AsyncIterator[AudioFrame]:
        for i, path in enumerate(self._paths):
            if i == 0 and self._first is not None:
                source = self._first
                self._first = None
            else:
                source = FileAudioSource(path, self._blocksize, self._channel)
            if source.sample_rate != self.sample_rate:
                await source.close()
                raise ValueError(
                    f"{path}: {source.sample_rate} Hz in a "
                    f"{self.sample_rate} Hz corpus — mixed rates indicate a "
                    "recording-setup problem; fix the corpus"
                )
            logger.info("corpus.playing", file=path.name, index=i, of=len(self._paths))
            async for frame in source.frames():
                yield frame
            await source.close()

    async def close(self) -> None:
        if self._first is not None:
            await self._first.close()


class LiveAudioSource:
    """sounddevice input stream: the radio, via the USB interface.

    Requirements from docs/roadmap.md Phase 2:
    - device selected by name substring (ALSA names differ from Core Audio,
      so an index alone is not portable)
    - slice `use_channel`, never average (docs/hardware.md §3.7)
    - `input_gain_db` applied post-capture, warning above ~12 dB because
      that much digital gain means an analog problem upstream
    - survive device disconnect/reconnect without killing the process —
      the capture loop reopens the stream with backoff, loudly
    """

    # Reconnect backoff bounds. Genuine constants: they trade log noise
    # against reconnect latency, nothing rig-specific.
    _RETRY_INITIAL_S = 1.0
    _RETRY_MAX_S = 30.0
    _GAIN_WARN_DB = 12.0

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self.name = "live"
        self.sample_rate = cfg.capture_rate
        self._gain = float(10.0 ** (cfg.input_gain_db / 20.0))
        self._closed = False
        # Bounded handoff from the PortAudio callback thread to asyncio.
        # Deliberately generous: it only ever backs up if the event loop
        # stalls, and dropping capture audio is worse than a little memory.
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=256)
        self._dropped_frames = 0
        if cfg.input_gain_db > self._GAIN_WARN_DB:
            logger.warning(
                "audio.high_digital_gain",
                input_gain_db=cfg.input_gain_db,
                hint="that much post-capture gain means the analog level is "
                "wrong — fix it at the radio/interface first "
                "(docs/hardware.md §3.5)",
            )

    async def frames(self) -> AsyncIterator[AudioFrame]:
        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def callback(
            indata: npt.NDArray[np.float32],
            frame_count: int,
            time_info: object,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                logger.warning("audio.callback_status", status=str(status))
            # Slice one channel, never average (docs/hardware.md §3.7).
            pcm = indata[:, self._cfg.use_channel].copy()
            if self._gain != 1.0:
                pcm = np.clip(pcm * self._gain, -1.0, 1.0)
            frame = AudioFrame(
                pcm=pcm,
                sample_rate=self.sample_rate,
                captured_at=datetime.now(UTC),
                source_name=self.name,
            )
            loop.call_soon_threadsafe(self._enqueue, frame)

        retry_s = self._RETRY_INITIAL_S
        while not self._closed:
            try:
                device = resolve_input_device(self._cfg.device)
                stream = sd.InputStream(
                    device=device,
                    channels=self._cfg.input_channels,
                    samplerate=self._cfg.capture_rate,
                    blocksize=self._cfg.blocksize,
                    dtype="float32",
                    callback=callback,
                )
                with stream:
                    logger.info(
                        "audio.capture_started",
                        device=self._cfg.device or "(default)",
                        rate=self._cfg.capture_rate,
                        use_channel=self._cfg.use_channel,
                    )
                    retry_s = self._RETRY_INITIAL_S
                    while not self._closed:
                        try:
                            frame = await asyncio.wait_for(
                                self._queue.get(), timeout=5.0
                            )
                        except TimeoutError:
                            if not stream.active:
                                # Device went away mid-stream (USB unplug).
                                raise sd.PortAudioError(
                                    "input stream went inactive"
                                ) from None
                            continue  # just a quiet stretch; keep waiting
                        yield frame
            except (sd.PortAudioError, RuntimeError, OSError) as e:
                if self._closed:
                    return
                # Loud, always: a dead capture chain that reconnects
                # silently would hide a flaky cable until it fails for good.
                logger.error(
                    "audio.capture_lost",
                    error=str(e),
                    retry_in_s=retry_s,
                )
                await asyncio.sleep(retry_s)
                retry_s = min(retry_s * 2, self._RETRY_MAX_S)

    def _enqueue(self, frame: AudioFrame) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Never block the audio callback; drop the oldest and count it.
            self._dropped_frames += 1
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(frame)
            logger.warning("audio.frames_dropped", total_dropped=self._dropped_frames)

    async def close(self) -> None:
        self._closed = True
