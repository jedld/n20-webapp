"""WAV resampling utilities for TTS voice references.

Resamples a reference WAV (or converts MP3) to match the target sample rate
expected by the TTS engine (e.g., 16kHz → 24kHz for Qwen3-TTS via vLLM-Omni).
"""

from __future__ import annotations

import os
import shutil
import subprocess
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


def _cache_wav_path(src: Path, target_sample_rate: int, cache_dir: str) -> Path:
    mtime = int(src.stat().st_mtime_ns)
    return Path(cache_dir) / f"{src.stem}_{mtime}_{target_sample_rate}.wav"


def _ffmpeg_to_wav(src: Path, dst: Path, target_sample_rate: int) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-ac",
                "1",
                "-ar",
                str(target_sample_rate),
                str(dst),
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return dst.is_file()


def _pydub_to_wav(src: Path, dst: Path, target_sample_rate: int) -> bool:
    try:
        from pydub import AudioSegment
    except ImportError:
        return False
    try:
        audio = AudioSegment.from_file(str(src))
        audio = audio.set_channels(1).set_frame_rate(int(target_sample_rate))
        dst.parent.mkdir(parents=True, exist_ok=True)
        audio.export(str(dst), format="wav")
    except Exception:
        return False
    return dst.is_file()


def convert_audio_to_wav(
    input_path: str,
    target_sample_rate: int,
    output_path: str,
) -> str:
    """Decode MP3 (or other compressed audio) to a mono WAV at *target_sample_rate*."""
    src = Path(input_path)
    dst = Path(output_path)
    if _ffmpeg_to_wav(src, dst, target_sample_rate):
        return str(dst)
    if _pydub_to_wav(src, dst, target_sample_rate):
        return str(dst)
    raise RuntimeError(
        f"Cannot decode {src} to WAV at {target_sample_rate} Hz. "
        "Install ffmpeg (recommended) or pydub."
    )


def ensure_sample_rate(
    wav_path: str,
    target_sample_rate: int,
    *,
    cache_dir: Optional[str] = None,
) -> str:
    """Return a WAV at *target_sample_rate*.

    WAV files already at the target rate are returned as-is. MP3 and other
    compressed samples are decoded to WAV. Cached conversions include the
    source mtime so replacing a clip invalidates the cache.
    """
    src = Path(wav_path)
    suffix = src.suffix.lower()

    cache_path: Optional[Path] = None
    if cache_dir:
        cache_path = _cache_wav_path(src, target_sample_rate, cache_dir)
        if cache_path.is_file():
            try:
                if get_wav_sample_rate(str(cache_path)) == target_sample_rate:
                    return str(cache_path)
            except Exception:
                pass

    if suffix != ".wav":
        if cache_path is None:
            fd, tmp = tempfile.mkstemp(
                suffix=f"_resampled_{target_sample_rate}.wav",
                prefix="n20_tts_",
            )
            os.close(fd)
            cache_path = Path(tmp)
        return convert_audio_to_wav(str(src), target_sample_rate, str(cache_path))

    src_rate = get_wav_sample_rate(str(src))
    if src_rate == target_sample_rate:
        return str(src)

    if cache_path is not None:
        return resample_wav(str(src), target_sample_rate, str(cache_path))
    return resample_wav(str(src), target_sample_rate)
