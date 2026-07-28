#!/usr/bin/env python3
"""Slice a long ambient MP3 to <= max duration at a seamless loop point.

Finds a cut where the tail matches the intro and the last sample is close to
the first (for HTML5 ``audio.loop`` playback).

Usage:
    python slice_loopable_audio.py input.mp3 output.mp3
    python slice_loopable_audio.py input.mp3 output.mp3 --max-sec 300 --backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from pydub import AudioSegment


def find_loop_point(
    samples: np.ndarray,
    sr: int,
    *,
    max_sec: float,
    min_sec: float,
    ref_sec: float,
    step_sec: float,
) -> tuple[float, dict]:
    ref_n = int(ref_sec * sr)
    min_i = int(min_sec * sr)
    max_i = int(max_sec * sr)
    step = max(1, int(step_sec * sr))
    ref = samples[:ref_n] - samples[:ref_n].mean()
    ref_norm = float(np.linalg.norm(ref)) + 1e-9
    hop = sr // 4
    rms = [
        float(np.sqrt(np.mean(samples[i : i + hop] ** 2)))
        for i in range(0, len(samples) - hop, hop)
    ]

    best = None
    for end in range(min_i, max_i, step):
        tail = samples[max(0, end - ref_n) : end]
        if len(tail) < ref_n:
            continue
        tail = tail[-ref_n:]
        tail = tail - tail.mean()
        corr = float(np.dot(ref, tail) / (ref_norm * (float(np.linalg.norm(tail)) + 1e-9)))
        seam = abs(float(samples[end - 1]) - float(samples[0]))
        quiet = rms[min(len(rms) - 1, (end - 1) // hop)]
        score = corr * 2.0 - seam * 3.0 - quiet * 0.25
        if best is None or score > best[0]:
            best = (score, corr, seam, quiet, end / sr)

    if best is None:
        raise RuntimeError("no loop point found in search window")
    _, corr, seam, quiet, loop_sec = best
    return loop_sec, {
        "correlation": round(corr, 4),
        "seam_jump_last_to_first": round(seam, 6),
        "rms_at_cut": round(quiet, 6),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-sec", type=float, default=300.0)
    parser.add_argument("--min-sec", type=float, default=60.0)
    parser.add_argument("--ref-sec", type=float, default=2.0)
    parser.add_argument("--step-sec", type=float, default=0.1)
    parser.add_argument("--backup", action="store_true", help="Copy input to <input>_full<ext> before slicing")
    parser.add_argument("--meta", type=Path, help="Write loop metadata JSON (default: <output>.loop.json)")
    args = parser.parse_args(argv)

    src = args.input.resolve()
    dst = args.output.resolve()
    if not src.is_file():
        print(f"error: not found: {src}", file=sys.stderr)
        return 1

    if args.backup:
        backup = src.with_name(f"{src.stem}_full{src.suffix}")
        if not backup.exists():
            shutil.copy2(src, backup)
            print(f"backup: {backup}")

    audio = AudioSegment.from_file(src).set_channels(1).set_frame_rate(22050)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    if audio.sample_width == 2:
        samples /= 32768.0
    sr = audio.frame_rate

    loop_sec, metrics = find_loop_point(
        samples,
        sr,
        max_sec=args.max_sec,
        min_sec=args.min_sec,
        ref_sec=args.ref_sec,
        step_sec=args.step_sec,
    )

    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-t",
            f"{loop_sec:.3f}",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(dst),
        ],
        check=True,
    )

    meta_path = args.meta or dst.with_suffix(".loop.json")
    payload = {
        "source": str(src),
        "loop_seconds": round(loop_sec, 3),
        "output": str(dst),
        "max_seconds": args.max_sec,
        "metrics": metrics,
    }
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
