"""Tests for voice baking helpers and clone resolution."""

from pathlib import Path

from webapp.tts.voice_baking import (
    discover_baked_sample,
    should_bake_qwen3_design_voice,
    should_bake_voice_profile,
    voice_sample_path,
    voice_sample_relative_path,
)
from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy
from webapp.tts.voice_resolver import resolve_voice_plan


def test_voice_sample_paths(tmp_path):
    rel = voice_sample_relative_path("pip_barmaid")
    assert rel.as_posix() == "assets/voice_samples/pip_barmaid.wav"
    path = voice_sample_path(str(tmp_path), "pip_barmaid")
    assert path == tmp_path / rel


def test_discover_baked_sample_by_entity_uid(tmp_path):
    sample = tmp_path / voice_sample_relative_path("mara_bartender")
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFF")
    assert discover_baked_sample(str(tmp_path), "mara_bartender") == str(sample)


def test_discover_baked_sample_falls_back_to_type(tmp_path):
    from natural20.tts.campaign_voice_profiles import _slug

    npc_type = "pip"
    type_uid = f"type_{_slug(npc_type)}"
    sample = tmp_path / voice_sample_relative_path(type_uid)
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFF")
    assert discover_baked_sample(str(tmp_path), "pip_barmaid", npc_type=npc_type) == str(sample)
    assert discover_baked_sample(str(tmp_path), "pip_barmaid") is None


def test_should_bake_when_design_and_no_reference(tmp_path):
    from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy

    profile = VoiceProfile(
        npc_uid="npc-1",
        prompt="Warm barmaid voice",
        strategy=VoiceStrategy.DESIGN,
        campaign_root=str(tmp_path),
        locked=True,
    )
    assert should_bake_voice_profile(profile)
    assert should_bake_qwen3_design_voice(
        resolved_strategy="preset",
        reference_audio=None,
        campaign_root=str(tmp_path),
        npc_uid="npc-1",
        profile_strategy="design",
    )


def test_should_not_bake_when_sample_exists(tmp_path):
    from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy

    sample = tmp_path / voice_sample_relative_path("npc-1")
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFF")
    profile = VoiceProfile(
        npc_uid="npc-1",
        prompt="Warm barmaid voice",
        strategy=VoiceStrategy.DESIGN,
        campaign_root=str(tmp_path),
    )
    assert not should_bake_voice_profile(profile)
    assert not should_bake_qwen3_design_voice(
        resolved_strategy="design",
        reference_audio=None,
        campaign_root=str(tmp_path),
        npc_uid="npc-1",
    )


def test_load_baked_ref_text_defaults(tmp_path):
    from webapp.tts.voice_baking import bake_sample_text, load_baked_ref_text, save_baked_ref_text

    uid = "mara_bartender"
    save_baked_ref_text(str(tmp_path), uid, "Custom calibration line.")
    assert load_baked_ref_text(str(tmp_path), uid) == "Custom calibration line."
    assert load_baked_ref_text(str(tmp_path), "missing_npc") == bake_sample_text()


def test_resolver_prefers_clone_when_baked_sample_present(tmp_path):
    ref = tmp_path / "assets/voice_samples/npc-1.wav"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"RIFF")
    profile = VoiceProfile(
        npc_uid="npc-1",
        prompt="Cheerful barmaid",
        reference_audio=str(ref),
        strategy=VoiceStrategy.DESIGN,
    )
    engine = type("E", (), {"_mode": "voice_design", "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"})()
    plan = resolve_voice_plan(profile, "qwen3", engine=engine)
    assert plan.strategy == VoiceStrategy.CLONE
    assert plan.reference_audio == str(ref)
