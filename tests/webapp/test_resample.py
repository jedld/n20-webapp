import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from webapp.tts.resample import ensure_sample_rate, get_wav_sample_rate, resample_wav


def _write_sine_wav(path: str, sample_rate: int, duration_s: float = 1.0, freq: float = 440.0) -> None:
    """Write a simple mono 16-bit WAV with a sine wave."""
    n_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    samples = (np.sin(2 * np.pi * freq * t) * 30000).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def test_get_wav_sample_rate(tmp_path):
    wav = str(tmp_path / "test.wav")
    _write_sine_wav(wav, 16000)
    assert get_wav_sample_rate(wav) == 16000


def test_resample_wav_16k_to_24k(tmp_path):
    src = str(tmp_path / "src_16k.wav")
    dst = str(tmp_path / "dst_24k.wav")
    _write_sine_wav(src, 16000, duration_s=0.5)

    result = resample_wav(src, 24000, dst)
    assert result == dst
    assert get_wav_sample_rate(dst) == 24000

    # Original has 16000 * 0.5 = 8000 frames; resampled has ~12000
    with wave.open(dst, "rb") as wf:
        assert wf.getframerate() == 24000
        expected_frames = int(round(8000 * 24000 / 16000))
        actual_frames = wf.getnframes()
        assert abs(actual_frames - expected_frames) <= 2


def test_resample_wav_already_matching_rate(tmp_path):
    src = str(tmp_path / "src_24k.wav")
    _write_sine_wav(src, 24000)

    result = resample_wav(src, 24000)
    # Should return the original path when rates match
    assert result == src


def test_ensure_sample_rate_no_resample_needed(tmp_path):
    src = str(tmp_path / "src.wav")
    _write_sine_wav(src, 24000)

    result = ensure_sample_rate(src, 24000)
    assert result == src


def test_ensure_sample_rate_resamples_and_caches(tmp_path):
    src = str(tmp_path / "src_16k.wav")
    cache_dir = str(tmp_path / "cache")
    _write_sine_wav(src, 16000, duration_s=0.5)

    # First call: resample and cache
    result1 = ensure_sample_rate(src, 24000, cache_dir=cache_dir)
    assert result1 != src
    assert get_wav_sample_rate(result1) == 24000

    # Second call: use cached version
    result2 = ensure_sample_rate(src, 24000, cache_dir=cache_dir)
    assert result2 == result1


def test_ensure_sample_rate_no_cache_dir(tmp_path):
    src = str(tmp_path / "src_16k.wav")
    _write_sine_wav(src, 16000)

    result = ensure_sample_rate(src, 24000)
    assert result != src
    assert get_wav_sample_rate(result) == 24000
    # Result should be a temp file that exists
    assert Path(result).is_file()


def test_ensure_sample_rate_cache_invalidates_on_mtime(tmp_path):
    src = tmp_path / "src_16k.wav"
    cache_dir = tmp_path / "cache"
    _write_sine_wav(str(src), 16000, duration_s=0.2)

    result1 = ensure_sample_rate(str(src), 24000, cache_dir=str(cache_dir))
    later = src.stat().st_mtime + 5
    os.utime(src, (later, later))
    result2 = ensure_sample_rate(str(src), 24000, cache_dir=str(cache_dir))
    assert Path(result1).name != Path(result2).name
    assert get_wav_sample_rate(result2) == 24000


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ensure_sample_rate_converts_mp3(tmp_path):
    wav = tmp_path / "src.wav"
    mp3 = tmp_path / "src.mp3"
    _write_sine_wav(str(wav), 16000, duration_s=0.2)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav), str(mp3)],
        check=True,
        capture_output=True,
    )
    result = ensure_sample_rate(str(mp3), 24000, cache_dir=str(tmp_path / "cache"))
    assert Path(result).suffix.lower() == ".wav"
    assert get_wav_sample_rate(result) == 24000
