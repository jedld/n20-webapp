"""WAV resampling utilities for TTS voice references.

Resamples a reference WAV file to match the target sample rate expected
by the TTS engine (e.g., 16kHz → 24kHz for Qwen3-TTS via vLLM-Omni).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import resample


def get_wav_sample_rate(wav_path: str) -> int:
    """Return the sample rate of a WAV file without loading audio data."""
    with wave.open(str(wav_path), "rb") as wf:
        return wf.getframerate()


def resample_wav(
    input_path: str,
    target_sample_rate: int,
    output_path: Optional[str] = None,
) -> str:
    """Resample a mono 16-bit WAV to *target_sample_rate*.

    Returns the path to the resampled WAV (either *output_path* or a
    temporary file that the caller must clean up).
    """
    input_path = str(input_path)

    with wave.open(input_path, "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        src_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # Skip resampling if the rate already matches
    if src_rate == target_sample_rate:
        if output_path:
            shutil.copy2(input_path, output_path)
            return output_path
        return input_path

    # Decode to float64 for resampling
    if sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16)
    elif sample_width == 4:
        samples = np.frombuffer(raw, dtype=np.int32)
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    # Resample
    num_new_samples = int(round(samples.shape[0] * target_sample_rate / src_rate))
    resampled = resample(samples, num_new_samples)

    # Convert back to int16 (clip to avoid overflow)
    if samples.dtype == np.int32:
        resampled_i16 = np.clip(resampled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)
    else:
        resampled_i16 = np.clip(resampled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)

    # Write output
    if output_path is None:
        fd, output_path = tempfile.mkstemp(
            suffix=f"_resampled_{target_sample_rate}.wav",
            prefix="n20_tts_",
        )
        os.close(fd)

    # Ensure parent directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with wave.open(output_path, "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(target_sample_rate)
        wf.writeframes(resampled_i16.tobytes())

    return output_path


def ensure_sample_rate(
    wav_path: str,
    target_sample_rate: int,
    *,
    cache_dir: Optional[str] = None,
) -> str:
    """Return *wav_path* as-is if it matches *target_sample_rate*, otherwise
    return a resampled copy.

    When *cache_dir* is provided, the resampled file is written there with a
    deterministic name derived from the source filename and target rate, so
    repeated calls reuse the cached result.
    """
    src_rate = get_wav_sample_rate(wav_path)
    if src_rate == target_sample_rate:
        return wav_path

    src_name = Path(wav_path).stem

    # Build a cache key from the source file to avoid repeated resampling
    if cache_dir:
        cache_path = Path(cache_dir) / f"{src_name}_{target_sample_rate}.wav"
        if cache_path.is_file():
            cached_rate = get_wav_sample_rate(str(cache_path))
            if cached_rate == target_sample_rate:
                return str(cache_path)
        return resample_wav(wav_path, target_sample_rate, str(cache_path))

    return resample_wav(wav_path, target_sample_rate)
