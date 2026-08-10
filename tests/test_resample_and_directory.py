"""Phase 2 audio, hardware-free parts: the resampling wrapper and the
corpus DirectoryAudioSource. LiveAudioSource itself needs a device and is
verified during hardware bring-up (docs/hardware.md §5)."""

import asyncio
from pathlib import Path

import numpy as np
import pytest

from tests.synth import write_wav16
from vhfwatch.audio.resample import ResamplingAudioSource
from vhfwatch.audio.sources import DirectoryAudioSource, FileAudioSource
from vhfwatch.models import AudioFrame


async def collect(source) -> list[AudioFrame]:
    frames = [f async for f in source.frames()]
    await source.close()
    return frames


def make_48k_tone(path: Path, seconds: float = 1.0, freq: float = 400.0) -> None:
    n = int(48000 * seconds)
    t = np.arange(n, dtype=np.float32) / 48000
    write_wav16(
        path, (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32), rate=48000
    )


def test_resamples_48k_to_16k(tmp_path: Path) -> None:
    make_48k_tone(tmp_path / "capture.wav", seconds=1.0)
    inner = FileAudioSource(tmp_path / "capture.wav")
    assert inner.sample_rate == 48000

    source = ResamplingAudioSource(inner, target_rate=16000)
    assert source.sample_rate == 16000
    frames = asyncio.run(collect(source))

    total = sum(len(f.pcm) for f in frames)
    # 1 s of audio in, ~1 s out at the new rate (minus the filter tail).
    assert total == pytest.approx(16000, abs=800)
    assert all(f.sample_rate == 16000 for f in frames)

    # The tone must survive: RMS of a 0.5-amplitude sine is ~0.354.
    pcm = np.concatenate([f.pcm for f in frames])
    rms = float(np.sqrt(np.mean(pcm[2000:-2000] ** 2)))
    assert rms == pytest.approx(0.354, abs=0.02)


def test_passthrough_when_rate_already_matches(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample.wav"
    inner = FileAudioSource(fixture)
    source = ResamplingAudioSource(inner, target_rate=inner.sample_rate)
    frames = asyncio.run(collect(source))
    assert len(frames) > 0
    assert frames[0].sample_rate == inner.sample_rate


def test_directory_source_plays_in_filename_order(tmp_path: Path) -> None:
    # record_corpus.py names files by start timestamp, so filename order
    # is timestamp order — the property replay depends on.
    make_48k_tone(tmp_path / "20260810-120100.wav", seconds=0.1, freq=300)
    make_48k_tone(tmp_path / "20260810-120000.wav", seconds=0.1, freq=500)

    source = DirectoryAudioSource(tmp_path)
    assert source.sample_rate == 48000
    frames = asyncio.run(collect(source))
    assert len(frames) > 0
    # First frames come from the 120000 file (the 500 Hz tone): count the
    # zero crossings of the first block to identify it.
    first = frames[0].pcm
    crossings = int(np.sum(np.abs(np.diff(np.signbit(first)))))
    freq_estimate = crossings / 2 / (len(first) / 48000)
    assert freq_estimate == pytest.approx(500, rel=0.1)


def test_directory_source_refuses_mixed_rates(tmp_path: Path) -> None:
    make_48k_tone(tmp_path / "a.wav", seconds=0.1)
    write_wav16(tmp_path / "b.wav", np.zeros(1600, dtype=np.float32), rate=16000)

    source = DirectoryAudioSource(tmp_path)
    with pytest.raises(ValueError, match="mixed rates"):
        asyncio.run(collect(source))


def test_empty_directory_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DirectoryAudioSource(tmp_path)
