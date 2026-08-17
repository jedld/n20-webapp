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


def test_create_voice_from_profile_qwen3_yaml_uses_active_vllm(tmp_path):
    """NPC YAML ``provider: qwen3`` must stay on the running vLLM sidecar."""
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3_vllm")

    profile = build_voice_profile_from_entity(
        SimpleNamespace(
            entity_uid="npc_lenka_ash",
            properties={
                "voice": {
                    "prompt": "Wry locksmith, light dry mezzo",
                    "provider": "qwen3",
                    "strategy": "design",
                },
            },
        )
    )
    config = manager.create_voice_from_profile(profile)
    assert manager._resolve_runtime_provider("qwen3") == "mock_qwen3_vllm"
    assert profile.npc_uid in manager.get_engine().list_locked_voices()
    assert "qwen3" not in manager._extra_engines
    assert config.extra_metadata.get("provider") in ("mock_qwen3_vllm", "qwen3")


def test_bake_voice_cosyvoice_while_runtime_is_qwen3(tmp_path):
    """CosyVoice models the reference WAV; Qwen3 remains the runtime engine."""
    from pathlib import Path

    from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy

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
        provider_override="mock_cosyvoice",
    )
    assert path and Path(path).is_file()
    ref = Path(path).with_suffix(".ref.txt").read_text(encoding="utf-8").lower()
    assert "good afternoon" in ref
    assert "gravelly" not in ref
    assert manager._provider_name == "mock_qwen3"
    assert "old_tam" in manager.get_engine_for_provider("mock_cosyvoice").list_locked_voices()


def test_resolve_bake_provider_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("N20_TTS_BAKE_PROVIDER", "mock_cosyvoice")
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3")
    assert manager._resolve_bake_provider(None) == "mock_cosyvoice"
    assert manager._resolve_runtime_provider("qwen3") == "mock_qwen3"


def test_ensure_npc_voice_locked_refreshes_when_profile_changes(tmp_path):
    from webapp.tts.npc_voice import ensure_npc_voice_locked
    from webapp.tts.voice_baking import save_voice_profile_state, voice_sample_relative_path

    campaign = tmp_path / "camp"
    sample = campaign / voice_sample_relative_path("npc_mara")
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFF")
    save_voice_profile_state(
        str(campaign),
        "npc_mara",
        fingerprint="oldfingerprintxx",
        audio_path=str(sample),
        source="baked",
    )

    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3_vllm")
    entity = SimpleNamespace(
        entity_uid="npc_mara",
        session=SimpleNamespace(root_path=str(campaign)),
        properties={
            "label": "Mara",
            "gender": "female",
            "voice": {
                "prompt": "Stout bartender with a new british accent",
                "strategy": "clone",
                "locked": True,
            },
        },
    )
    ensure_npc_voice_locked(manager, entity)
    assert "npc_mara" in manager.list_locked_voices()

    engine = manager.get_engine()
    create_calls = {"n": 0}
    original = engine.create_voice

    def counting_create(*args, **kwargs):
        create_calls["n"] += 1
        return original(*args, **kwargs)

    engine.create_voice = counting_create
    ensure_npc_voice_locked(manager, entity)
    assert create_calls["n"] == 0

    entity.properties["voice"]["prompt"] = "Completely different elderly whisper"
    ensure_npc_voice_locked(manager, entity)
    assert create_calls["n"] == 1
