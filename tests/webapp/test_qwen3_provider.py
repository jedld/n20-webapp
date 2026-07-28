"""Tests for Qwen3 TTS provider registration and mock synthesis."""

from webapp.tts.manager import TTSManager
from webapp.tts.model_config import get_model_config, resolve_model_profile
from webapp.tts.qwen3_provider import MockQwen3Provider, _normalize_tts_device, _pick_speaker


def test_qwen3_registered_in_manager(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    assert "qwen3" in manager.PROVIDERS
    assert "mock_qwen3" in manager.PROVIDERS


def test_mock_qwen3_initialize_and_generate(tmp_path):
    manager = TTSManager(cache_dir=str(tmp_path / "tts"))
    manager.initialize(provider="mock_qwen3")
    voice = manager.create_voice("npc-1", "A gruff dwarf blacksmith")
    path = manager.generate("Hello there.", voice.npc_uid)
    assert path.endswith(".wav")


def test_resolve_qwen3_profile():
    engine = MockQwen3Provider(cache_dir="/tmp/qwen3_profile_test")
    assert resolve_model_profile(engine, "qwen3") == "qwen3_custom_voice"
    cfg = get_model_config(engine, "qwen3")
    assert cfg.profile == "qwen3_custom_voice"
    assert cfg.supports_streaming is True


def test_pick_speaker_stable_per_npc():
    a = _pick_speaker("npc-abc", "male")
    b = _pick_speaker("npc-abc", "male")
    c = _pick_speaker("npc-xyz", "male")
    assert a == b
    assert a in {"Ryan", "Aiden", "Uncle_Fu", "Dylan", "Eric"}


def test_normalize_tts_device_accepts_gpu_alias():
    assert _normalize_tts_device("gpu") == "cuda"
    assert _normalize_tts_device("CUDA") == "cuda"
    assert _normalize_tts_device("cuda:1") == "cuda:1"
    assert _normalize_tts_device("cpu") == "cpu"


def test_manager_normalizes_gpu_device(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_DEVICE", "gpu")
    manager = TTSManager(cache_dir=str(tmp_path / "tts"), device="gpu")
    assert manager.device == "cuda"
