"""Tests for multi-backend voice profile and resolver."""

from types import SimpleNamespace

from webapp.tts.capabilities import get_provider_capabilities
from webapp.tts.npc_voice import build_voice_profile_from_entity
from webapp.tts.voice_profile import VoiceProfile, VoiceStrategy
from webapp.tts.voice_baking import bake_sample_text, should_bake_qwen3_design_voice
from webapp.tts.voice_resolver import resolve_voice_plan


class TestVoiceProfileDesignPrompt:
    def test_design_prompt_merges_traits_and_demographics(self):
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Gravelly dockside barkeeper",
            gender="male",
            age_band="mature",
            race="human",
            traits=["dry humor", "low voice"],
            accent="irish",
            label="Mara",
        )
        design = profile.design_prompt()
        assert "barkeeper" in design.lower()
        assert "male" in design.lower()
        assert "dry humor" in design.lower()
        assert "irish" in design.lower()
        assert design.lower().index("irish") < design.lower().index("barkeeper")
        assert "not american" in design.lower()

    def test_design_prompt_leads_with_british_accent(self):
        profile = VoiceProfile(
            npc_uid="npc-2",
            prompt="Brisk wool merchant",
            gender="female",
            accent="british",
        )
        design = profile.design_prompt()
        assert design.lower().startswith("british english accent")
        assert "not american" in design.lower()
        assert "non-rhotic" in design.lower()

    def test_design_prompt_eastern_european_is_specific(self):
        profile = VoiceProfile(
            npc_uid="npc_vampire",
            prompt="Ancient aristocratic vampire lord",
            gender="male",
            accent="eastern european",
        )
        design = profile.design_prompt()
        assert "eastern european" in design.lower()
        assert "romanian" in design.lower() or "transylvanian" in design.lower()
        assert "not american" in design.lower()
        assert design.lower().index("eastern european") < design.lower().index("vampire")

    def test_design_prompt_skips_american_accent_clause(self):
        profile = VoiceProfile(
            npc_uid="npc-3",
            prompt="Sunny farmhand",
            accent="american",
        )
        design = profile.design_prompt()
        assert "american accent" not in design.lower()


class TestBakeSampleText:
    def test_british_bake_line_is_not_gravelly_american(self):
        text = bake_sample_text("british")
        assert "good afternoon" in text.lower()
        assert "gravelly" not in text.lower()

    def test_eastern_european_bake_line(self):
        text = bake_sample_text("eastern european")
        assert "slowly and clearly" in text.lower()


class TestExternalBakeProvider:
    def test_cosyvoice_bake_provider_skips_qwen3_voicedesign(self, monkeypatch, tmp_path):
        monkeypatch.setenv("N20_TTS_BAKE_PROVIDER", "cosyvoice")
        assert should_bake_qwen3_design_voice(
            resolved_strategy="design",
            reference_audio=None,
            campaign_root=str(tmp_path),
            npc_uid="npc_vampire_count_aldegrim",
        ) is False

    def test_elevenlabs_bake_provider_skips_qwen3_voicedesign(self, monkeypatch, tmp_path):
        monkeypatch.setenv("N20_TTS_BAKE_PROVIDER", "elevenlabs")
        assert should_bake_qwen3_design_voice(
            resolved_strategy="design",
            reference_audio=None,
            campaign_root=str(tmp_path),
            npc_uid="npc_vampire_count_aldegrim",
        ) is False
        monkeypatch.delenv("N20_TTS_BAKE_PROVIDER", raising=False)
        assert should_bake_qwen3_design_voice(
            resolved_strategy="design",
            reference_audio=None,
            campaign_root=str(tmp_path),
            npc_uid="npc_vampire_count_aldegrim",
        ) is True


class TestVoiceResolver:
    def test_cosyvoice_defaults_to_instruct_without_reference(self):
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Nervous young thief",
            gender="male",
        )
        plan = resolve_voice_plan(profile, "cosyvoice")
        assert plan.strategy == VoiceStrategy.INSTRUCT

    def test_qwen3_custom_voice_defaults_to_preset(self):
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Wise elderly elf sage",
            gender="female",
        )
        engine = SimpleNamespace(_mode="custom_voice", model_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        plan = resolve_voice_plan(profile, "qwen3", engine=engine)
        assert plan.strategy == VoiceStrategy.PRESET
        assert "elf" in plan.design_prompt.lower()

    def test_qwen3_voice_design_model_uses_design_strategy(self):
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Sinister undead whisper",
            gender="male",
            traits=["raspy", "cold"],
        )
        engine = SimpleNamespace(_mode="voice_design", model_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
        plan = resolve_voice_plan(profile, "qwen3", engine=engine)
        assert plan.strategy == VoiceStrategy.DESIGN
        assert plan.voice_prompt == plan.design_prompt

    def test_explicit_clone_when_reference_present(self, tmp_path):
        ref = tmp_path / "sample.wav"
        ref.write_bytes(b"RIFF")  # existence check only
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Villain lord",
            reference_audio=str(ref),
            strategy=VoiceStrategy.AUTO,
        )
        plan = resolve_voice_plan(profile, "cosyvoice")
        assert plan.strategy == VoiceStrategy.CLONE
        assert plan.reference_audio == str(ref)

    def test_explicit_design_downgrades_on_cosyvoice(self):
        profile = VoiceProfile(
            npc_uid="npc-1",
            prompt="Cheerful halfling merchant",
            strategy=VoiceStrategy.DESIGN,
        )
        plan = resolve_voice_plan(profile, "cosyvoice")
        assert plan.strategy == VoiceStrategy.INSTRUCT


class TestBuildVoiceProfileFromEntity:
    def test_loads_strategy_traits_and_provider(self):
        entity = SimpleNamespace(
            entity_uid="npc_test",
            properties={
                "label": "Grimjaw",
                "gender": "male",
                "voice": {
                    "prompt": "Gruff orc warrior",
                    "strategy": "design",
                    "provider": "qwen3",
                    "traits": ["gravelly", "booming"],
                    "accent": "american",
                    "language": "en",
                },
            },
        )
        profile = build_voice_profile_from_entity(entity)
        assert profile.npc_uid == "npc_test"
        assert profile.strategy == VoiceStrategy.DESIGN
        assert profile.provider == "qwen3"
        assert profile.traits == ["gravelly", "booming"]
        assert "gruff orc" in profile.design_prompt().lower()

    def test_eastern_european_accent_leads_entity_design_prompt(self):
        entity = SimpleNamespace(
            entity_uid="npc_vampire_count_aldegrim",
            properties={
                "label": "Count Aldegrim",
                "gender": "male",
                "voice": {
                    "prompt": "Ancient aristocratic vampire lord",
                    "accent": "eastern european",
                    "language": "en",
                },
            },
        )
        profile = build_voice_profile_from_entity(entity)
        design = profile.design_prompt().lower()
        assert design.startswith("english with a clear eastern european")
        assert "romanian" in design or "transylvanian" in design
        assert "not american" in design


class TestProviderCapabilities:
    def test_qwen3_supports_design_and_preset(self):
        caps = get_provider_capabilities("qwen3")
        assert caps.supports_design is True
        assert caps.supports_preset is True

    def test_cosyvoice_does_not_support_design(self):
        caps = get_provider_capabilities("cosyvoice")
        assert caps.supports_design is False
        assert VoiceStrategy.INSTRUCT in caps.supported_strategies
