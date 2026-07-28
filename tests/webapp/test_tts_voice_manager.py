"""Tests for create_voice_from_profile on TTSManager."""

from types import SimpleNamespace

from webapp.tts.manager import TTSManager
from webapp.tts.npc_voice import build_voice_profile_from_entity


def test_create_voice_from_profile_mock_qwen3(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3")

    entity = SimpleNamespace(
        entity_uid="npc_mara",
        properties={
            "label": "Mara",
            "gender": "female",
            "voice": {
                "prompt": "Stout bartender with dry humor",
                "traits": ["gravelly", "warm"],
                "strategy": "preset",
            },
        },
    )
    profile = build_voice_profile_from_entity(entity)
    config = manager.create_voice_from_profile(profile)

    assert config.npc_uid == "npc_mara"
    meta = config.extra_metadata or {}
    assert meta.get("resolved_strategy") == "preset"
    assert meta.get("design_prompt")


def test_create_voice_from_profile_respects_provider_override(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_cosyvoice")

    profile = build_voice_profile_from_entity(
        SimpleNamespace(
            entity_uid="npc_elf",
            properties={
                "voice": {
                    "prompt": "Ethereal elven sage",
                    "provider": "mock_qwen3",
                    "strategy": "preset",
                },
            },
        )
    )
    config = manager.create_voice_from_profile(profile)
    engine = manager.get_engine_for_provider("mock_qwen3")
    assert profile.npc_uid in engine.list_locked_voices()
    assert config.npc_uid == "npc_elf"
