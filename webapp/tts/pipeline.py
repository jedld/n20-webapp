"""Prefetch helpers for multi-segment TTS streaming."""
from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional, TypeVar

T = TypeVar('T')

SegmentPcm = list[tuple[int, bytes]]


def pipeline_segments_enabled() -> bool:
    raw = os.environ.get('N20_TTS_PIPELINE_SEGMENTS', '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off', 'disabled')


def pipeline_concurrent_enabled() -> bool:
    """When true, the next segment may synthesize while the current one streams."""
    raw = os.environ.get('N20_TTS_PIPELINE_CONCURRENT', '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off', 'disabled')


class SegmentPrefetcher:
    """Run segment synthesis on a worker thread, one segment ahead when possible."""

    def __init__(
        self,
        synthesize: Callable[[str], SegmentPcm],
        *,
        concurrent: bool = True,
        inference_lock: Optional[threading.Lock] = None,
    ):
        self._synthesize = synthesize
        self._concurrent = concurrent
        self._inference_lock = inference_lock or threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix='n20-tts-seg',
        )
        self._pending: Optional[Future[SegmentPcm]] = None

    def _run_synthesis(self, chunk_text: str) -> SegmentPcm:
        if self._concurrent:
            return self._synthesize(chunk_text)
        with self._inference_lock:
            return self._synthesize(chunk_text)

    def schedule(self, chunk_text: str) -> None:
        if self._pending is not None:
            raise RuntimeError('segment prefetch already scheduled')
        self._pending = self._executor.submit(self._run_synthesis, chunk_text)

    def take(self) -> SegmentPcm:
        if self._pending is None:
            raise RuntimeError('no prefetched segment available')
        try:
            return self._pending.result()
        finally:
            self._pending = None

    def has_pending(self) -> bool:
        return self._pending is not None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
