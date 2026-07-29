"""Tests for vLLM stream PCM defaults and campaign voice registration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from webapp.conversation_service import ConversationService
from webapp.tts import preferences


def _service(provider: str = "qwen3_vllm"):
    return SimpleNamespace(_tts_manager=Mock(_provider_name=provider))


@pytest.mark.parametrize(
    ("provider", "delivery", "env_flag", "expected"),
    [
        ("qwen3_vllm", "stream", None, True),
        ("qwen3_vllm", "async", None, False),
        ("qwen3_vllm", "stream", "0", False),
        ("qwen3_vllm", "stream", "1", True),
        ("cosyvoice", "async", None, True),
        ("mock_qwen3_vllm", "stream", None, True),
    ],
)
def test_stream_pcm_to_socket_enabled(provider, delivery, env_flag, expected, monkeypatch):
    if env_flag is None:
        monkeypatch.delenv("VLLM_OMNI_TTS_SOCKET_PCM", raising=False)
    else:
        monkeypatch.setenv("VLLM_OMNI_TTS_SOCKET_PCM", env_flag)
    preferences.refresh_campaign_delivery_mode({"tts_delivery": delivery})
    service = _service(provider)
    assert ConversationService._stream_pcm_to_socket_enabled(service) is expected


def test_register_campaign_voices_skips_missing_samples(tmp_path):
    from webapp.tts.vllm_campaign_voices import register_campaign_voices

    summary = register_campaign_voices(tmp_path)
    assert summary.total == 0
    assert summary.uploaded == 0


def test_register_campaign_voices_uploads_missing_voice(tmp_path, monkeypatch):
    from webapp.tts.vllm_campaign_voices import register_campaign_voices

    samples = tmp_path / "assets" / "voice_samples"
    samples.mkdir(parents=True)
    wav = samples / "mara_bartender.wav"
    wav.write_bytes(b"RIFF")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"voices": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, timeout=30.0):
            return FakeResponse()

        def post(self, url, data=None, files=None):
            return FakeResponse()

    monkeypatch.setenv("VLLM_OMNI_TTS_URL", "http://127.0.0.1:8091")
    with patch("httpx.Client", FakeClient):
        summary = register_campaign_voices(tmp_path)
    assert summary.total == 1
    assert summary.uploaded == 1
    assert summary.failed == 0


def test_register_campaign_voices_if_needed_respects_provider(tmp_path, monkeypatch):
    from webapp.tts.vllm_campaign_voices import register_campaign_voices_if_needed

    monkeypatch.setenv("VLLM_OMNI_TTS_REGISTER_ON_START", "1")
    with patch("webapp.tts.vllm_campaign_voices.register_campaign_voices") as mocked:
        mocked.return_value = Mock(uploaded=0, skipped=0, failed=0, total=0)
        assert register_campaign_voices_if_needed(tmp_path, provider_name="qwen3_vllm") is not None
        mocked.assert_called_once()
        mocked.reset_mock()
        assert register_campaign_voices_if_needed(tmp_path, provider_name="cosyvoice") is None
        mocked.assert_not_called()
