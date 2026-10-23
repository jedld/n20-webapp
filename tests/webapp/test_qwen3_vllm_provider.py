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
    voices_response.json.return_value = {"voices": [{"name": "n20_missing_on_disk_xyz"}]}
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
    provider.create_voice("missing_on_disk_xyz", "Warm tavern keeper")
    mock_http.post.assert_not_called()


def test_generate_posts_speech_request(tmp_path):
    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": [{"name": "n20_npc"}]}
    speech_response = MagicMock()
    speech_response.status_code = 200
    speech_response.text = ""
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


def test_ensure_registered_does_not_fake_server_voice_without_ref_audio(tmp_path):
    """Missing clone samples must not be treated as uploaded ICL voices."""
    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": []}
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

    registered = provider._ensure_registered(
        "n20_missing_voice_uid_xyz",
        reference_audio=None,
        ref_text="A test voice",
        npc_uid="missing_voice_uid_xyz",
        campaign_root=str(tmp_path / "campaign"),
        npc_type=None,
    )

    assert registered is False
    assert "n20_missing_voice_uid_xyz" not in provider._server_voices
    mock_http.post.assert_not_called()

    provider.create_voice("missing_voice_uid_xyz", "Wry locksmith")
    with pytest.raises(ValueError, match="not registered"):
        provider.generate("Hello there.", "missing_voice_uid_xyz")


def test_discover_reference_audio_finds_campaign_sample(tmp_path):
    """_discover_reference_audio should find a baked sample in campaign/assets/voice_samples/."""
    campaign = tmp_path / "campaign"
    samples_dir = campaign / "assets" / "voice_samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "pia_bernal.wav").write_bytes(b"RIFF" + b"\x00" * 40)

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
    provider = MockQwen3VLLMProvider(cache_dir=str(tmp_path / "cache"))
    provider._config = config

    result = provider._discover_reference_audio(
        "pia_bernal", None, str(campaign), npc_type=None
    )
    assert result is not None
    assert "pia_bernal.wav" in result


def test_discover_reference_audio_explicit_path_wins(tmp_path):
    """When an explicit path is provided and exists, it should be returned."""
    explicit_wav = tmp_path / "explicit.wav"
    explicit_wav.write_bytes(b"RIFF" + b"\x00" * 40)

    provider = MockQwen3VLLMProvider(cache_dir=str(tmp_path / "cache"))

    result = provider._discover_reference_audio(
        "some_npc", str(explicit_wav), None, npc_type=None
    )
    assert result == str(explicit_wav)


def test_discover_reference_audio_cross_campaign(tmp_path):
    """Without campaign_root, discovery still searches user_levels voice samples."""
    provider = MockQwen3VLLMProvider(cache_dir=str(tmp_path / "cache"))

    missing = provider._discover_reference_audio(
        "missing_voice_uid_xyz", None, None, npc_type=None
    )
    assert missing is None

    found = provider._discover_reference_audio(
        "npc_lenka_ash", None, None, npc_type=None
    )
    if found:
        # Discovery picks the newest sample per stem; campaign asset
        # refreshes convert references between wav and mp3, so only the
        # stem is stable (the provider resamples either format to 24k).
        import os
        stem = os.path.splitext(os.path.basename(found))[0]
        assert stem == "npc_lenka_ash"
        assert found.lower().endswith((".wav", ".mp3"))


def test_generate_retries_with_inline_ref_audio_on_clone_400(tmp_path):
    """Listed-but-not-ICL speakers must retry Base clone with the local WAV."""
    ref_wav = tmp_path / "npc_lenka_ash.wav"
    ref_wav.write_bytes(_minimal_wav_bytes())
    (tmp_path / "npc_lenka_ash.ref.txt").write_text(
        "A wry locksmith greeting.", encoding="utf-8"
    )

    mock_http = MagicMock()
    voices_response = MagicMock()
    voices_response.raise_for_status.return_value = None
    voices_response.json.return_value = {"voices": [{"name": "n20_npc_lenka_ash"}]}
    mock_http.get.return_value = voices_response

    bad_speech = MagicMock()
    bad_speech.status_code = 400
    bad_speech.text = (
        "Base task with built-in speaker 'n20_npc_lenka_ash' "
        "requires 'ref_audio' for voice cloning"
    )
    register_response = MagicMock()
    register_response.status_code = 200
    register_response.raise_for_status.return_value = None
    good_speech = MagicMock()
    good_speech.status_code = 200
    good_speech.text = ""
    good_speech.content = _minimal_wav_bytes()
    good_speech.raise_for_status.return_value = None

    def post(url, **kwargs):
        if url == "/v1/audio/voices":
            return register_response
        payload = kwargs.get("json") or {}
        if payload.get("ref_audio"):
            return good_speech
        return bad_speech

    mock_http.post.side_effect = post

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
    provider.create_voice(
        "npc_lenka_ash",
        "Wry locksmith",
        reference_audio=str(ref_wav),
        voice_metadata={"campaign_root": str(tmp_path)},
    )

    out = provider.generate("Hello from the tavern.", "npc_lenka_ash")
    assert Path(out).is_file()
    speech_payloads = [
        call.kwargs.get("json")
        for call in mock_http.post.call_args_list
        if call.args and call.args[0] == "/v1/audio/speech"
    ]
    assert speech_payloads
    assert any(payload.get("ref_audio") for payload in speech_payloads)
    assert "n20_npc_lenka_ash" in provider._needs_ref_audio


def test_discover_reference_audio_finds_mp3(tmp_path):
    campaign = tmp_path / "campaign"
    samples_dir = campaign / "assets" / "voice_samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "pia_bernal.mp3").write_bytes(b"ID3fake")

    provider = MockQwen3VLLMProvider(cache_dir=str(tmp_path / "cache"))
    result = provider._discover_reference_audio(
        "pia_bernal", None, str(campaign), npc_type=None
    )
    assert result is not None
    assert result.endswith("pia_bernal.mp3")


def test_create_voice_reregisters_when_sample_replaced(tmp_path):
    campaign = tmp_path / "campaign"
    samples = campaign / "assets" / "voice_samples"
    samples.mkdir(parents=True)
    ref_wav = samples / "mara_bartender.wav"
    ref_wav.write_bytes(_minimal_wav_bytes())

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
    meta = {"campaign_root": str(campaign), "design_prompt": "Warm tavern keeper"}
    provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata=meta,
    )
    assert mock_http.post.call_count == 1

    mock_http.post.reset_mock()
    provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata=meta,
    )
    mock_http.post.assert_not_called()

    ref_wav.write_bytes(_minimal_wav_bytes() + b"\x01\x00" * 20)
    mock_http.post.reset_mock()
    provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata=meta,
    )
    assert mock_http.post.call_count == 1
    assert mock_http.post.call_args.args[0] == "/v1/audio/voices"


def test_create_voice_reregisters_when_profile_changes(tmp_path):
    campaign = tmp_path / "campaign"
    samples = campaign / "assets" / "voice_samples"
    samples.mkdir(parents=True)
    ref_wav = samples / "mara_bartender.wav"
    ref_wav.write_bytes(_minimal_wav_bytes())

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
    provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata={"campaign_root": str(campaign), "design_prompt": "old prompt"},
    )
    mock_http.post.reset_mock()
    provider.create_voice(
        "mara_bartender",
        "Warm tavern keeper",
        reference_audio=str(ref_wav),
        voice_metadata={"campaign_root": str(campaign), "design_prompt": "new british accent prompt"},
    )
    assert mock_http.post.call_count == 1
    assert mock_http.post.call_args.args[0] == "/v1/audio/voices"


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
