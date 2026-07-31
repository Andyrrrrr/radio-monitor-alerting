"""FileAudioSource: WAV in, AudioFrames out."""

import asyncio
import wave
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from tests.synth import BASE, RATE, silence, tone, write_wav16
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.models import AudioFrame


def collect(source: FileAudioSource) -> list[AudioFrame]:
    async def _run() -> list[AudioFrame]:
        out = [frame async for frame in source.frames()]
        await source.close()
        return out

    return asyncio.run(_run())


def test_yields_whole_file_in_order(tmp_path: Path) -> None:
    pcm = np.concatenate([tone(500, -20.0), silence(250)])
    path = tmp_path / "mono.wav"
    write_wav16(path, pcm)

    source = FileAudioSource(path, blocksize=1024, start_at=BASE)
    assert source.sample_rate == RATE
    assert source.name == "file:mono.wav"

    got = collect(source)
    total = np.concatenate([f.pcm for f in got])
    assert len(total) == len(pcm)
    # 16-bit round trip: writer scales by 32767, reader divides by 32768,
    # so the worst case is a hair over one quantization step.
    assert np.max(np.abs(total - pcm)) < 2 / 32768

    # captured_at advances by exactly the samples yielded.
    assert got[0].captured_at == BASE
    assert got[1].captured_at == BASE + timedelta(seconds=1024 / RATE)


def test_stereo_slices_requested_channel(tmp_path: Path) -> None:
    # Left = tone, right = silence: the 2-channel-interfaces case. Averaging
    # instead of slicing would show up here as a halved signal.
    left = tone(200, -20.0)
    right = silence(200)
    interleaved = np.empty(len(left) * 2, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right
    path = tmp_path / "stereo.wav"
    write_wav16(path, interleaved, channels=2)

    live = collect(FileAudioSource(path, channel=0, start_at=BASE))
    quiet = collect(FileAudioSource(path, channel=1, start_at=BASE))
    assert np.max(np.abs(np.concatenate([f.pcm for f in live]))) > 0.1
    assert np.max(np.abs(np.concatenate([f.pcm for f in quiet]))) == 0.0


def test_missing_channel_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mono.wav"
    write_wav16(path, silence(100))
    with pytest.raises(ValueError, match="channel 1"):
        FileAudioSource(path, channel=1)


def test_unsupported_sample_width_rejected(tmp_path: Path) -> None:
    path = tmp_path / "eight_bit.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)  # 8-bit
        wf.setframerate(RATE)
        wf.writeframes(bytes(1600))
    with pytest.raises(ValueError, match="16-bit"):
        FileAudioSource(path)
