"""Helpers for chunked PCM TTS streaming."""
from __future__ import annotations

import base64
import struct
import wave
from pathlib import Path
from typing import Iterator, List, Optional


def silence_pcm_bytes(sample_rate: int, duration_ms: int, channels: int = 1) -> bytes:
    frames = int(sample_rate * max(0, duration_ms) / 1000)
    return b'\x00\x00' * frames * channels


def concat_wav_files(
    wav_paths: List[str],
    output_path: str,
    pause_ms: int = 0,
) -> str:
    """Concatenate mono/stereo 16-bit WAV files with an optional pause between segments."""
    if not wav_paths:
        raise ValueError('wav_paths must not be empty')
    if len(wav_paths) == 1:
        src = Path(wav_paths[0])
        dst = Path(output_path)
        if src.resolve() != dst.resolve():
            dst.write_bytes(src.read_bytes())
        return str(dst)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Read first file params from opening segment.
    with wave.open(str(wav_paths[0]), 'rb') as first:
        channels = first.getnchannels()
        sample_width = first.getsampwidth()
        sample_rate = first.getframerate()
        if sample_width != 2:
            raise ValueError(f'Unsupported WAV sample width: {sample_width}')

    pause_frame = silence_pcm_bytes(sample_rate, pause_ms, channels=channels) if pause_ms > 0 else b''

    with wave.open(str(out), 'w') as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(sample_width)
        out_wf.setframerate(sample_rate)
        for index, wav_path in enumerate(wav_paths):
            with wave.open(str(wav_path), 'rb') as wf:
                if (
                    wf.getnchannels() != channels
                    or wf.getsampwidth() != sample_width
                    or wf.getframerate() != sample_rate
                ):
                    raise ValueError(f'WAV format mismatch for {wav_path}')
                out_wf.writeframes(wf.readframes(wf.getnframes()))
            if pause_frame and index < len(wav_paths) - 1:
                out_wf.writeframes(pause_frame)
    return str(out)


def iter_wav_pcm_chunks(
    wav_path: str,
    chunk_samples: int = 4096,
) -> Iterator[tuple[int, bytes]]:
    """Yield (sample_rate, pcm_bytes) chunks from a WAV file."""
    with wave.open(str(wav_path), 'rb') as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        if sample_width != 2:
            raise ValueError(f'Unsupported WAV sample width: {sample_width}')
        frame_bytes = sample_width * channels
        while True:
            raw = wf.readframes(chunk_samples)
            if not raw:
                break
            if channels > 1:
                # Downmix to mono int16
                count = len(raw) // frame_bytes
                mono = bytearray()
                for i in range(count):
                    offset = i * frame_bytes
                    left = struct.unpack_from('<h', raw, offset)[0]
                    right = struct.unpack_from('<h', raw, offset + 2)[0] if channels > 1 else left
                    mono.extend(struct.pack('<h', int((left + right) / 2)))
                yield sample_rate, bytes(mono)
            else:
                yield sample_rate, raw


def pcm_b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode('ascii')


def write_pcm_chunks_to_wav(
    chunks: list[bytes],
    output_path: str,
    sample_rate: int,
) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for chunk in chunks:
            wf.writeframes(chunk)
    return str(path)


def iter_rechunked_pcm(
    pcm_iter: Iterator[bytes],
    sample_rate: int,
    chunk_samples: int = 4096,
) -> Iterator[tuple[int, bytes]]:
    """Buffer arbitrary PCM byte chunks into fixed-size int16 mono frames."""
    buffer = bytearray()
    chunk_bytes = max(2, int(chunk_samples)) * 2
    for piece in pcm_iter:
        if not piece:
            continue
        buffer.extend(piece)
        while len(buffer) >= chunk_bytes:
            yield sample_rate, bytes(buffer[:chunk_bytes])
            del buffer[:chunk_bytes]
    if buffer:
        yield sample_rate, bytes(buffer)
