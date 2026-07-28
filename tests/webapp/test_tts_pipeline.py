import threading
import time

import pytest

from webapp.tts.manager import TTSManager
from webapp.tts.model_config import get_model_config
from webapp.tts.text_chunking import split_tts_text
from webapp.tts.cosyvoice_provider import MockCosyVoiceProvider


class SlowSegmentMockProvider(MockCosyVoiceProvider):
    """Mock provider that sleeps per segment to exercise prefetch overlap."""

    def __init__(self, *args, segment_delay_s: float = 0.08, **kwargs):
        super().__init__(*args, **kwargs)
        self._segment_delay_s = segment_delay_s
        self._segment_lock = threading.Lock()
        self._active_segments = 0
        self._max_active_segments = 0

    def generate_stream(
        self,
        text,
        npc_uid,
        emotion=None,
        output_path=None,
        instruct=None,
        volume=None,
        chunk_samples=4096,
    ):
        with self._segment_lock:
            self._active_segments += 1
            self._max_active_segments = max(
                self._max_active_segments,
                self._active_segments,
            )
        try:
            time.sleep(self._segment_delay_s)
            yield from super().generate_stream(
                text,
                npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
                chunk_samples=chunk_samples,
            )
        finally:
            with self._segment_lock:
                self._active_segments -= 1


@pytest.fixture
def slow_manager(tmp_path):
    provider = SlowSegmentMockProvider(
        cache_dir=str(tmp_path / 'cache'),
        segment_delay_s=0.12,
    )
    provider.initialize()
    provider.create_voice('npc1', 'test voice')

    manager = TTSManager(cache_dir=str(tmp_path / 'cache'))
    manager._engine = provider
    manager._provider_name = 'mock_cosyvoice'
    manager._auto_create_on_first_speech = True
    return manager, provider


def _long_text(num_segments: int = 4) -> str:
    from webapp.tts.model_config import MODEL_PROFILES

    max_chars = MODEL_PROFILES['mock_cosyvoice'].max_chars
    sentence = 'This is spoken line number zero for pipeline testing.'
    parts = []
    while len(' '.join(parts)) < max_chars * num_segments:
        parts.append(sentence.replace('zero', str(len(parts))))
    return ' '.join(parts)


def test_pipelined_stream_matches_sequential_output(slow_manager, monkeypatch):
    manager, _provider = slow_manager
    monkeypatch.setenv('N20_TTS_PIPELINE_SEGMENTS', '1')
    monkeypatch.setenv('N20_TTS_PIPELINE_CONCURRENT', '0')
    monkeypatch.setenv('N20_TTS_CHUNK_PAUSE_MS', '0')

    text = _long_text(3)
    cfg = get_model_config(manager.get_engine(), 'mock_cosyvoice')
    chunks = split_tts_text(text, config=cfg)
    assert len(chunks) >= 2

    sequential = list(
        manager._generate_stream_sequential(
            manager.get_engine(),
            chunks,
            'npc1',
            pause_ms=0,
        )
    )
    pipelined = list(manager.generate_stream(text, 'npc1'))

    assert len(sequential) == len(pipelined)
    assert sequential == pipelined


def test_pipelined_concurrent_overlaps_segment_synthesis(slow_manager, monkeypatch):
    manager, provider = slow_manager
    monkeypatch.setenv('N20_TTS_PIPELINE_SEGMENTS', '1')
    monkeypatch.setenv('N20_TTS_PIPELINE_CONCURRENT', '1')
    monkeypatch.setenv('N20_TTS_CHUNK_PAUSE_MS', '0')

    text = _long_text(4)
    chunks = split_tts_text(text, config=get_model_config(manager.get_engine(), 'mock_cosyvoice'))
    assert len(chunks) >= 3

    started = time.monotonic()
    pcm_chunks = list(manager.generate_stream(text, 'npc1'))
    elapsed = time.monotonic() - started

    assert pcm_chunks
    sequential_estimate = provider._segment_delay_s * len(chunks)
    assert elapsed < sequential_estimate
    assert provider._max_active_segments >= 2


def test_pipeline_disabled_uses_sequential_path(slow_manager, monkeypatch):
    manager, provider = slow_manager
    monkeypatch.setenv('N20_TTS_PIPELINE_SEGMENTS', '0')

    text = _long_text(2)
    provider._max_active_segments = 0
    list(manager.generate_stream(text, 'npc1'))

    assert provider._max_active_segments <= 1
