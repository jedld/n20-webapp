"""Tests for Qwen3 vLLM-Omni HTTP TTS provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webapp.tts.manager import TTSManager
from webapp.tts.model_config import get_model_config, resolve_model_profile
from webapp.tts.qwen3_vllm_provider import (
    MockQwen3VLLMProvider,
    Qwen3VLLMProvider,
    VLLMOmniTTSClient,
    VLLMOmniTTSConfig,
    _estimate_max_new_tokens,
    _parse_voice_names,
)


def test_qwen3_vllm_registered_in_manager(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    assert "qwen3_vllm" in manager.PROVIDERS
    assert "mock_qwen3_vllm" in manager.PROVIDERS


def test_parse_voice_names_dict_and_list():
    assert _parse_voice_names({"voices": [{"name": "n20_mara"}, "ryan"]}) == {
        "n20_mara",
        "ryan",
    }
    assert _parse_voice_names([{"id": "alice"}, "bob"]) == {"alice", "bob"}


def test_mock_qwen3_vllm_initialize_and_generate(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3_vllm")
    voice = manager.create_voice("mara_bartender", "Warm tavern keeper")
    path = manager.generate("Hello there.", voice.npc_uid)
    assert path.endswith(".wav")
    assert Path(path).is_file()


def test_resolve_qwen3_vllm_profile():
    engine = MockQwen3VLLMProvider(cache_dir="/tmp/vllm_profile_test")
    assert resolve_model_profile(engine, "qwen3_vllm") == "qwen3_vllm"
    cfg = get_model_config(engine, "qwen3_vllm")
    assert cfg.profile == "qwen3_vllm"
    assert cfg.supports_streaming is True


def test_create_voice_registers_when_missing(tmp_path):
    ref_wav = tmp_path / "mara.wav"
    ref_wav.write_bytes(b"RIFF" + b"\x00" * 40)
    ref_txt = tmp_path / "mara.ref.txt"
    ref_txt.write_text("Reference line.", encoding="utf-8")

    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": []}
    register_response = MagicMock()
    register_response.raise_for_status.return_value = None
    mock_http.get.return_value = voices_response
    mock_http.post.return_value = register_response

    config = VLLMOmniTTSConfig(
        base_url="http://testserver:8091",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        api_key="none",
        timeout=30.0,
        voice_prefix="n20_",
        consent="test",
        register_on_create=True,
        sample_rate=24000,
        stream_live=True,
        max_new_tokens=2048,
    )
    client = VLLMOmniTTSClient(config, client=mock_http)
    provider = Qwen3VLLMProvider(cache_dir=str(tmp_path / "cache"), client=client)
    provider.initialize()

    voice = provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata={"ref_text": "Reference line."},
    )
    assert voice.extra_metadata["vllm_voice_name"] == "n20_mara_bartender"
    assert mock_http.post.call_count == 1
    post_args, post_kwargs = mock_http.post.call_args
    assert post_args[0] == "/v1/audio/voices"
    assert post_kwargs["data"]["name"] == "n20_mara_bartender"


def test_create_voice_skips_register_when_already_on_server(tmp_path):
    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": [{"name": "n20_mara_bartender"}]}
    mock_http.get.return_value = voices_response

    config = VLLMOmniTTSConfig(
        base_url="http://testserver:8091",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        api_key="none",
        timeout=30.0,
        voice_prefix="n20_",
        consent="test",
        register_on_create=True,
        sample_rate=24000,
        stream_live=True,
        max_new_tokens=2048,
    )
    client = VLLMOmniTTSClient(config, client=mock_http)
    provider = Qwen3VLLMProvider(cache_dir=str(tmp_path / "cache"), client=client)
    provider.initialize()
    provider.create_voice("mara_bartender", "Warm tavern keeper")
    mock_http.post.assert_not_called()


def test_generate_posts_speech_request(tmp_path):
    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": [{"name": "n20_npc"}]}
    speech_response = MagicMock()
    speech_response.raise_for_status.return_value = None
    speech_response.content = _minimal_wav_bytes()
    mock_http.get.return_value = voices_response
    mock_http.post.return_value = speech_response

    config = VLLMOmniTTSConfig(
        base_url="http://testserver:8091",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        api_key="none",
        timeout=30.0,
        voice_prefix="n20_",
        consent="test",
        register_on_create=True,
        sample_rate=24000,
        stream_live=True,
        max_new_tokens=2048,
    )
    client = VLLMOmniTTSClient(config, client=mock_http)
    provider = Qwen3VLLMProvider(cache_dir=str(tmp_path / "cache"), client=client)
    provider.initialize()
    provider.create_voice("npc", "Test voice")
    out = provider.generate("Hello.", "npc")
    assert Path(out).is_file()
    assert Path(out).read_bytes() == _minimal_wav_bytes()
    speech_call = mock_http.post.call_args
    assert speech_call.args[0] == "/v1/audio/speech"
    payload = speech_call.kwargs["json"]
    assert payload["voice"] == "n20_npc"
    assert payload["input"] == "Hello."
    assert payload["response_format"] == "wav"
    assert payload["max_new_tokens"] >= 96


def test_estimate_max_new_tokens_scales_with_text():
    short = _estimate_max_new_tokens("Hello.", 2048)
    long = _estimate_max_new_tokens("x" * 200, 2048)
    assert short < long
    assert long <= 2048


def test_generate_stream_posts_pcm_stream_request(tmp_path):
    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": [{"name": "n20_npc"}]}
    mock_http.get.return_value = voices_response

    pcm_chunk_a = b"\x00\x01" * 128
    pcm_chunk_b = b"\x02\x03" * 128
    stream_response = MagicMock()
    stream_response.raise_for_status.return_value = None
    stream_response.iter_bytes.return_value = [pcm_chunk_a, pcm_chunk_b]
    stream_response.__enter__ = MagicMock(return_value=stream_response)
    stream_response.__exit__ = MagicMock(return_value=False)
    mock_http.stream.return_value = stream_response

    config = VLLMOmniTTSConfig(
        base_url="http://testserver:8091",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        api_key="none",
        timeout=30.0,
        voice_prefix="n20_",
        consent="test",
        register_on_create=True,
        sample_rate=24000,
        stream_live=True,
        max_new_tokens=2048,
    )
    client = VLLMOmniTTSClient(config, client=mock_http)
    provider = Qwen3VLLMProvider(cache_dir=str(tmp_path / "cache"), client=client)
    provider.initialize()
    provider.create_voice("npc", "Test voice")

    chunks = list(provider.generate_stream("Hello stream.", "npc", chunk_samples=64))
    assert chunks
    assert all(rate == 24000 for rate, _pcm in chunks)
    joined = b"".join(pcm for _rate, pcm in chunks)
    assert joined == pcm_chunk_a + pcm_chunk_b
    assert provider.last_stream_output_path()
    assert Path(provider.last_stream_output_path()).is_file()

    stream_call = mock_http.stream.call_args
    assert stream_call.args[0] == "POST"
    assert stream_call.args[1] == "/v1/audio/speech"
    payload = stream_call.kwargs["json"]
    assert payload["response_format"] == "pcm"
    assert payload["stream"] is True
    assert payload["stream_format"] == "audio"
    assert payload["max_new_tokens"] >= 96


def test_mock_qwen3_vllm_generate_stream_writes_wav(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3_vllm")
    manager.create_voice("npc", "Test")
    chunks = list(manager.generate_stream("Hello.", "npc"))
    assert len(chunks) >= 1
    assert manager._last_stream_output_path
    assert Path(manager._last_stream_output_path).is_file()


def test_initialize_marks_unready_when_healthcheck_fails(tmp_path):
    mock_http = MagicMock()
    mock_http.get.side_effect = RuntimeError("connection refused")
    config = VLLMOmniTTSConfig(
        base_url="http://testserver:8091",
        model="Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        api_key="none",
        timeout=30.0,
        voice_prefix="n20_",
        consent="test",
        register_on_create=True,
        sample_rate=24000,
        stream_live=True,
        max_new_tokens=2048,
    )
    client = VLLMOmniTTSClient(config, client=mock_http)
    provider = Qwen3VLLMProvider(cache_dir=str(tmp_path / "cache"), client=client)
    provider.initialize()
    assert provider.is_ready() is False
    with pytest.raises(RuntimeError, match="not ready"):
        provider.generate("Hello.", "npc")


def _minimal_wav_bytes() -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 100)
    return buf.getvalue()
