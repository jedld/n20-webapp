"""Tests for ElevenLabs bake-only Voice Design."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webapp.tts.elevenlabs_bake import (
    ElevenLabsBakeProvider,
    MockElevenLabsBakeProvider,
    elevenlabs_preview_text,
    elevenlabs_voice_description,
)
from webapp.tts.manager import TTSManager
from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy


def test_elevenlabs_preview_text_meets_api_minimum():
    text = elevenlabs_preview_text("british")
    assert len(text) >= 100
    assert len(text) <= 1000
    assert "good afternoon" in text.lower()


def test_elevenlabs_ee_preview_text_meets_api_minimum_without_pad():
    text = elevenlabs_preview_text("eastern european")
    assert 100 <= len(text) <= 1000
    assert "village" in text.lower()


def test_elevenlabs_voice_description_rewrites_vampire_for_safety():
    desc = elevenlabs_voice_description(
        "Ancient aristocratic vampire lord, cultured low baritone. "
        "undead fantasy character. speaking as Count Example",
        accent="eastern european",
    )
    assert "vampire" not in desc.lower()
    assert "undead" not in desc.lower()
    assert "speaking as" not in desc.lower()
    assert "noble lord" in desc.lower()
    assert "pale fantasy character" in desc.lower()
    desc = elevenlabs_voice_description(
        "Young swordsman, low earnest baritone",
        accent="eastern european",
    )
    assert desc.startswith("Native English.")
    assert "Thick Eastern European accent" in desc
    assert 20 <= len(desc) <= 1000


def test_mock_elevenlabs_bakes_mp3(tmp_path):
    campaign = tmp_path / "camp"
    provider = MockElevenLabsBakeProvider(cache_dir=str(tmp_path / "cache"))
    provider.initialize()
    path = provider.bake_voice_sample(
        "npc_mara",
        "Stout british bartender",
        campaign_root=str(campaign),
        accent="british",
    )
    assert path.endswith("npc_mara.mp3")
    assert Path(path).is_file()
    ref = Path(path).with_suffix(".ref.txt")
    assert ref.is_file()
    assert "good afternoon" in ref.read_text(encoding="utf-8").lower()


def test_elevenlabs_generate_is_refused(tmp_path):
    provider = MockElevenLabsBakeProvider(cache_dir=str(tmp_path / "cache"))
    provider.initialize()
    provider.create_voice("npc_mara", "Test")
    with pytest.raises(RuntimeError, match="bake-only"):
        provider.generate("Hello there.", "npc_mara")


def test_elevenlabs_design_posts_preview_and_writes_mp3(tmp_path):
    spoken = ("Good afternoon. " + ("I speak clearly. " * 8)).strip()
    audio = b"ID3fake-preview"
    mock_http = MagicMock()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "previews": [
            {
                "audio_base_64": base64.b64encode(audio).decode("ascii"),
                "generated_voice_id": "gen_1",
                "duration_secs": 2.4,
            }
        ],
        "text": spoken,
    }
    mock_http.post.return_value = response

    provider = ElevenLabsBakeProvider(cache_dir=str(tmp_path / "cache"), client=mock_http)
    provider.initialize()
    campaign = tmp_path / "camp"
    path = provider.bake_voice_sample(
        "old_tam",
        "Elderly British tavern regular, dry humor",
        campaign_root=str(campaign),
        accent="british",
    )
    assert Path(path).read_bytes() == audio
    assert Path(path).with_suffix(".ref.txt").read_text(encoding="utf-8") == spoken
    mock_http.post.assert_called_once()
    args, kwargs = mock_http.post.call_args
    assert args[0] == "/v1/text-to-voice/design"
    body = kwargs["json"]
    assert "Elderly British" in body["voice_description"]
    assert body["voice_description"].startswith("Native English.")
    assert 20 <= len(body["voice_description"]) <= 1000
    assert len(body["text"]) >= 100
    assert body["auto_generate_text"] is False
    assert body["model_id"] == "eleven_ttv_v3"
    assert body["guidance_scale"] == 5.0
    assert body["should_enhance"] is False
    assert kwargs["params"]["output_format"] == "mp3_24000_48"


def test_manager_rejects_elevenlabs_as_runtime_provider(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    with pytest.raises(ValueError, match="bake-only"):
        manager.initialize(provider="elevenlabs")


def test_manager_bakes_with_mock_elevenlabs_while_runtime_is_qwen3(tmp_path):
    campaign = tmp_path / "camp"
    (campaign / "assets" / "voice_samples").mkdir(parents=True)
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3")
    profile = VoiceProfile(
        npc_uid="old_tam",
        prompt="Elderly tavern regular",
        accent="british",
        gender="male",
        campaign_root=str(campaign),
        strategy=VoiceStrategy.DESIGN,
        provider="qwen3",
    )
    path = manager.bake_voice_for_profile(
        profile,
        force=True,
        provider_override="mock_elevenlabs",
    )
    assert path and Path(path).is_file()
    assert path.endswith(".mp3")
    assert manager._provider_name == "mock_qwen3"
    with pytest.raises(RuntimeError, match="bake-only"):
        manager.get_engine_for_provider("mock_elevenlabs").generate("Hi.", "old_tam")


def test_yaml_elevenlabs_provider_does_not_switch_runtime(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3_vllm")
    assert manager._resolve_runtime_provider("elevenlabs") == "mock_qwen3_vllm"
    assert manager._resolve_bake_provider("elevenlabs") == "elevenlabs"
