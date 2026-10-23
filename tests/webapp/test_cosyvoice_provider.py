"""
Tests for the CosyVoice TTS provider (MockCosyVoiceProvider).

Uses MockCosyVoiceProvider which generates placeholder WAV files
without requiring actual model inference.
"""

import os
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import pytest

from webapp.tts.base import VoiceConfig
from webapp.tts.cosyvoice_provider import CosyVoiceProvider, MockCosyVoiceProvider


# =====================================================================
# MockCosyVoiceProvider Tests
# =====================================================================


class TestMockCosyVoiceProvider:
    """Tests for the MockCosyVoiceProvider."""

    @pytest.fixture
    def provider(self, tmp_path):
        """Create a MockCosyVoiceProvider with a temporary cache directory."""
        cache_dir = str(tmp_path / "tts_test")
        return MockCosyVoiceProvider(cache_dir=cache_dir)

    def test_provider_name(self, provider):
        """Test that provider_name returns 'mock_cosyvoice'."""
        assert provider.provider_name == "mock_cosyvoice"

    def test_is_loaded(self, provider):
        """Test that the mock provider is pre-loaded."""
        assert provider._loaded is True

    def test_initialize_is_noop(self, provider):
        """Test that initialize() is a no-op for mock provider."""
        # Should not raise
        provider.initialize()
        assert provider._loaded is True

    def test_create_voice_basic(self, provider):
        """Test basic voice creation."""
        npc_uid = "npc_test_goblin"
        voice_prompt = "Greedy goblin with a raspy whisper"

        config = provider.create_voice(npc_uid, voice_prompt)

        assert config.npc_uid == npc_uid
        assert config.prompt_description == voice_prompt
        assert config.style == "lazy"  # "raspy" maps to "lazy"
        assert config.se_path is not None
        assert os.path.exists(config.se_path)

    def test_create_voice_multiple_npcs(self, provider):
        """Test creating voices for multiple NPCs."""
        npcs = [
            ("npc_goblin_001", "Greedy goblin with a raspy whisper"),
            ("npc_wizard_001", "Ethereal female wizard with soft voice"),
            ("npc_ogre_001", "Deep demonic ogre voice"),
        ]

        configs = {}
        for npc_uid, prompt in npcs:
            configs[npc_uid] = provider.create_voice(npc_uid, prompt)

        # All should have unique SE paths
        se_paths = [c.se_path for c in configs.values()]
        assert len(se_paths) == len(set(se_paths))

    def test_create_voice_with_reference_audio(self, provider):
        """Test voice creation with a reference audio file."""
        # Create a mock reference audio file
        ref_audio = str(provider.cache_dir / "reference.wav")
        provider._create_placeholder_wav(ref_audio)

        npc_uid = "npc_cloned_voice"
        config = provider.create_voice(npc_uid, "Test voice", reference_audio=ref_audio)

        assert config.npc_uid == npc_uid
        assert config.reference_audio == ref_audio
        assert os.path.exists(config.se_path)

    def test_create_voice_without_reference_raises_error(self, provider):
        """Test that creating a real CosyVoice voice without reference raises an error.
        
        Note: MockCosyVoiceProvider creates placeholder WAV files, so this test
        verifies the mock behavior (it does NOT require reference audio).
        """
        # MockCosyVoiceProvider does NOT require reference audio
        # (unlike the real CosyVoiceProvider)
        npc_uid = "npc_no_ref"
        config = provider.create_voice(npc_uid, "Test prompt")
        assert config.npc_uid == npc_uid

    def test_list_locked_voices_empty(self, provider):
        """Test listing locked voices when none exist."""
        assert provider.list_locked_voices() == []

    def test_list_locked_voices_after_creation(self, provider):
        """Test listing locked voices after creation."""
        provider.create_voice("npc_1", "Prompt 1")
        provider.create_voice("npc_2", "Prompt 2")

        voices = provider.list_locked_voices()
        assert set(voices) == {"npc_1", "npc_2"}

    def test_generate_basic(self, provider):
        """Test basic audio generation."""
        provider.create_voice("npc_test", "Test voice")

        output_path = provider.generate("Hello world", "npc_test")

        assert output_path is not None
        assert output_path.endswith(".wav")
        assert os.path.exists(output_path)

    def test_generate_with_custom_output_path(self, provider):
        """Test generation with custom output path."""
        provider.create_voice("npc_test", "Test voice")

        custom_path = str(provider.cache_dir / "custom_output.wav")
        result_path = provider.generate("Hello world", "npc_test", output_path=custom_path)

        assert result_path == custom_path
        assert os.path.exists(custom_path)

    def test_generate_without_voice_raises_error(self, provider):
        """Test that generating without a registered voice raises an error."""
        with pytest.raises(ValueError, match="No voice registered"):
            provider.generate("Hello world", "npc_nonexistent")

    def test_persist_voice(self, provider):
        """Test voice persistence."""
        provider.create_voice("npc_test", "Test voice")

        voice_data = provider.persist_voice("npc_test")

        assert "npc_uid" in voice_data
        assert voice_data["npc_uid"] == "npc_test"
        assert "se_path" in voice_data
        assert "style" in voice_data
        assert "prompt_description" in voice_data

    def test_persist_voice_unknown_npc(self, provider):
        """Test persisting an unknown NPC raises KeyError."""
        with pytest.raises(KeyError, match="No voice found"):
            provider.persist_voice("npc_nonexistent")

    def test_restore_voice(self, provider):
        """Test restoring a voice from saved data."""
        # Create and persist
        provider.create_voice("npc_test", "Test voice")
        voice_data = provider.persist_voice("npc_test")

        # Remove and restore
        provider.remove_voice("npc_test")
        assert "npc_test" not in provider.list_locked_voices()

        restored = provider.restore_voice("npc_test", voice_data)
        assert restored.npc_uid == "npc_test"
        assert "npc_test" in provider.list_locked_voices()

    def test_remove_voice(self, provider):
        """Test removing a voice."""
        provider.create_voice("npc_test", "Test voice")
        assert "npc_test" in provider.list_locked_voices()

        result = provider.remove_voice("npc_test")
        assert result is True
        assert "npc_test" not in provider.list_locked_voices()

    def test_remove_voice_unknown_npc(self, provider):
        """Test removing an unknown voice returns False."""
        result = provider.remove_voice("npc_nonexistent")
        assert result is False

    def test_status(self, provider):
        """Test status property."""
        provider.create_voice("npc_test", "Test voice")

        status = provider.status

        assert status["provider"] == "mock_cosyvoice"
        assert status["loaded"] is True
        assert status["voices"] == 1
        assert "cache_dir" in status

    def test_voice_config_serialization(self, provider):
        """Test VoiceConfig serialization/deserialization roundtrip."""
        provider.create_voice("npc_test", "Test voice", reference_audio=None)
        voice_data = provider.persist_voice("npc_test")

        # Verify dict structure
        assert isinstance(voice_data, dict)
        assert isinstance(voice_data["npc_uid"], str)
        assert isinstance(voice_data["se_path"], str)
        assert isinstance(voice_data["style"], str)
        assert isinstance(voice_data["prompt_description"], str)

    def test_voice_config_from_dict(self):
        """Test VoiceConfig.from_dict."""
        data = {
            "npc_uid": "npc_test",
            "se_path": "/tmp/test.se",
            "style": "angry",
            "prompt_description": "Test voice prompt",
            "reference_audio": "/tmp/reference.wav",
            "extra_metadata": {"key": "value"},
        }

        config = VoiceConfig.from_dict(data)

        assert config.npc_uid == "npc_test"
        assert config.se_path == "/tmp/test.se"
        assert config.style == "angry"
        assert config.reference_audio == "/tmp/reference.wav"
        assert config.extra_metadata == {"key": "value"}

    def test_voice_config_to_dict(self, provider):
        """Test VoiceConfig.to_dict."""
        provider.create_voice("npc_test", "Test voice")
        config = provider._voice_registry["npc_test"]

        data = config.to_dict()

        assert data["npc_uid"] == "npc_test"
        assert data["style"] == config.style
        assert data["prompt_description"] == "Test voice"


class TestMockCosyVoiceProviderEmotionClassification:
    """Tests for emotion/voice prompt classification."""

    @pytest.fixture
    def provider(self, tmp_path):
        cache_dir = str(tmp_path / "tts_test")
        return MockCosyVoiceProvider(cache_dir=cache_dir)

    @pytest.mark.parametrize(
        "prompt,expected_style",
        [
            ("raspy old man", "lazy"),
            ("gruff voice", "lazy"),
            ("rough whisper", "lazy"),
            ("aged wizard", "lazy"),
            ("wheezing elder", "lazy"),
            ("deep demon", "angry"),
            ("demonic growl", "angry"),
            ("menacing voice", "angry"),
            ("soft female voice", "calm"),
            ("gentle tone", "calm"),
            ("ghostly whisper", "whisper"),
            ("eerie voice", "whisper"),
            ("haunting tone", "whisper"),
            ("sad voice", "sad"),
            ("depressed tone", "sad"),
            ("fearful voice", "fearful"),
            ("scared tone", "fearful"),
            ("cheerful voice", "cheerful"),
            ("happy tone", "cheerful"),
            ("energetic voice", "cheerful"),
            ("loud voice", "angry"),
            ("boisterous tone", "angry"),
            ("default voice", "calm"),
        ],
    )
    def test_voice_prompt_classification(self, provider, prompt, expected_style):
        """Test voice prompt classification to style codes."""
        style = provider._classify_voice_prompt(prompt)
        assert style == expected_style


class TestMockCosyVoiceProviderPlaceholderWav:
    """Tests for placeholder WAV file generation."""

    @pytest.fixture
    def provider(self, tmp_path):
        cache_dir = str(tmp_path / "tts_test")
        return MockCosyVoiceProvider(cache_dir=cache_dir)

    def test_placeholder_wav_is_valid(self, provider, tmp_path):
        """Test that generated placeholder WAV files are valid."""
        wav_path = str(tmp_path / "test.wav")
        provider._create_placeholder_wav(wav_path)

        assert os.path.exists(wav_path)

        # Read and verify RIFF header
        with open(wav_path, "rb") as f:
            header = f.read(12)
            assert header.startswith(b"RIFF")
            assert header[8:] == b"WAVE"

    def test_placeholder_wav_contains_data_chunk(self, provider, tmp_path):
        """Test that placeholder WAV has data chunk."""
        wav_path = str(tmp_path / "test.wav")
        provider._create_placeholder_wav(wav_path)

        with open(wav_path, "rb") as f:
            content = f.read()
            assert b"data" in content


class TestMockCosyVoiceProviderEdgeCases:
    """Edge case tests for MockCosyVoiceProvider."""

    @pytest.fixture
    def provider(self, tmp_path):
        cache_dir = str(tmp_path / "tts_test")
        return MockCosyVoiceProvider(cache_dir=cache_dir)

    def test_generate_same_npc_multiple_times(self, provider):
        """Test generating audio for the same NPC multiple times."""
        provider.create_voice("npc_test", "Test voice")

        paths = []
        for text in ["Hello", "World", "Testing"]:
            path = provider.generate(text, "npc_test")
            paths.append(path)

        # Each call should generate a unique file
        assert len(paths) == len(set(paths))
        for path in paths:
            assert os.path.exists(path)

    def test_remove_and_regenerate_voice(self, provider):
        """Test removing and re-creating a voice."""
        # Create voice
        provider.create_voice("npc_test", "Original voice")
        original_se = provider._voice_registry["npc_test"].se_path

        # Remove
        provider.remove_voice("npc_test")

        # Re-create with different npc_uid (since MockCosyVoiceProvider uses npc_uid in path)
        provider.create_voice("npc_test_new", "New voice prompt")
        new_se = provider._voice_registry["npc_test_new"].se_path

        # Paths should be different because npc_uids are different
        assert "npc_test" in original_se
        assert "npc_test_new" in new_se
        assert original_se != new_se

    def test_restore_preserves_metadata(self, provider):
        """Test that restoring a voice preserves extra metadata."""
        # Create voice with metadata
        config = provider.create_voice("npc_test", "Test voice")
        config.extra_metadata = {"custom_key": "custom_value", "count": 42}
        voice_data = provider.persist_voice("npc_test")

        # Restore
        provider.remove_voice("npc_test")
        restored = provider.restore_voice("npc_test", voice_data)

        assert restored.extra_metadata == {"custom_key": "custom_value", "count": 42}


# =====================================================================
# CosyVoiceProvider Interface Tests (without actual model)
# =====================================================================


class TestCosyVoiceProviderInterface:
    """Tests for CosyVoiceProvider interface compliance.
    
    These tests verify the CosyVoiceProvider follows the TTSEngine
    interface without requiring actual model inference.
    """

    @pytest.fixture
    def provider(self, tmp_path, monkeypatch):
        """Create a CosyVoiceProvider with a temporary cache directory."""
        cache_dir = str(tmp_path / "tts_test")
        p = CosyVoiceProvider(cache_dir=cache_dir, device="cpu")
        # Never load real CosyVoice/OpenVoice weights in unit tests.
        monkeypatch.setattr(p, "_try_cosyvoice_init", lambda: False)
        monkeypatch.setattr(p, "_try_openvoice_fallback", lambda: False)
        return p

    def test_provider_name(self, provider):
        """Test that provider_name returns 'cosyvoice'."""
        assert provider.provider_name == "cosyvoice"

    def test_create_voice_uses_default_reference_when_available(self, provider, monkeypatch):
        """Without explicit reference, CosyVoice uses the bundled sample clip."""
        default_ref = None
        for root in CosyVoiceProvider._cosyvoice_checkout_roots():
            candidate = root / "asset" / "zero_shot_prompt.wav"
            if candidate.is_file():
                default_ref = candidate
                break
        if default_ref is None:
            with pytest.raises(ValueError, match="reference audio"):
                provider.create_voice("npc_test", "Test voice")
            return

        monkeypatch.setattr(provider, "_backend_ready", True)
        monkeypatch.setattr(provider, "_loaded", True)
        monkeypatch.setattr(provider, "_fallback_mode", False)
        config = provider.create_voice("npc_test", "Test voice")
        assert config.npc_uid == "npc_test"
        assert Path(config.se_path).exists()

    def test_create_voice_with_valid_reference(self, provider, tmp_path):
        """Test voice creation with a valid reference audio file."""
        # Create a mock reference audio file
        ref_audio = str(tmp_path / "reference.wav")
        import struct

        # Create minimal WAV
        data = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt "
        data += struct.pack("<HHIIHH", 1, 1, 16000, 16000, 1, 16)
        data += b"data" + struct.pack("<I", 0)
        with open(ref_audio, "wb") as f:
            f.write(data)

        provider._backend_ready = True
        provider._loaded = True
        config = provider.create_voice(
            "npc_test", "Test voice", reference_audio=ref_audio
        )

        assert config.npc_uid == "npc_test"
        assert config.reference_audio == ref_audio

    def test_create_voice_with_invalid_reference(self, provider):
        """Test voice creation with non-existent reference raises FileNotFoundError."""
        provider._backend_ready = True
        provider._loaded = True
        with pytest.raises(FileNotFoundError, match="Reference audio not found"):
            provider.create_voice("npc_test", "Test voice", reference_audio="/nonexistent.wav")

    def test_generate_requires_voice(self, provider):
        """Test that generate without a registered voice raises when backend is ready."""
        provider._backend_ready = True
        provider._loaded = True
        provider._fallback_mode = False
        with pytest.raises(ValueError, match="No voice registered"):
            provider.generate("Hello", "npc_nonexistent")

    def test_generate_without_backend_raises(self, provider):
        """Test that generate fails clearly when no speech backend is available."""
        provider._backend_ready = False
        provider._loaded = True
        with pytest.raises(RuntimeError, match="TTS not available"):
            provider.generate("Hello", "npc_nonexistent")

    def test_emotion_styles_mapping(self):
        """Test that EMOTION_STYLES contains expected keys."""
        styles = CosyVoiceProvider.EMOTION_STYLES

        # Check key D&D emotions are mapped
        assert "angry" in styles
        assert "cheerful" in styles
        assert "sad" in styles
        assert "fearful" in styles
        assert "calm" in styles
        assert "whisper" in styles

    def test_default_emotion(self):
        """Test DEFAULT_EMOTION is set."""
        assert CosyVoiceProvider.DEFAULT_EMOTION == "calm"

    def test_default_reference_audio_finds_repo_checkout(self, tmp_path):
        """Prompt WAVs live under the monorepo third_party/, not n20-webapp/."""
        roots = CosyVoiceProvider._cosyvoice_checkout_roots()
        if not any(root.name == "CosyVoice" and (root / "asset").is_dir() for root in roots):
            pytest.skip("third_party/CosyVoice checkout not present in this environment")
        provider = CosyVoiceProvider(device="cpu", cache_dir=str(tmp_path / "tts"))
        ref = provider._default_reference_audio("male")
        assert ref and Path(ref).is_file()
        assert ref.endswith(".wav")

    def test_accent_instruct_british(self, provider):
        """British accent should produce an English instruct clause."""
        text = provider._format_cosyvoice3_instruct("calm", accent="british", language="en")
        assert "British English" in text
        assert "<|endofprompt|>" in text
        assert text.startswith("You are a helpful assistant.")

    def test_accent_instruct_romanian_with_emotion(self, provider):
        text = provider._format_cosyvoice3_instruct("fearful", accent="romanian", language="en")
        assert "Romanian accent" in text
        assert "fearful" in text

    def test_instruct_override_from_llm(self, provider):
        text = provider._format_cosyvoice3_instruct(
            "calm",
            accent="british",
            language="en",
            instruct_override="trembling, barely holding back tears",
        )
        assert "British English" in text
        assert "trembling, barely holding back tears" in text
        assert "<|endofprompt|>" in text

    def test_whisper_volume_forces_soft_delivery(self, provider):
        text = provider._format_cosyvoice3_instruct(
            "fearful",
            accent="british",
            language="en",
            volume="whisper",
        )
        assert "very soft whispering voice" in text
        assert "fearfully" in text
        assert "loudly" not in text

    def test_whisper_volume_softens_loud_tts_instruct(self, provider):
        text = provider._format_cosyvoice3_instruct(
            "angry",
            volume="whisper",
            instruct_override="shout urgently at them",
        )
        assert "very soft whispering voice" in text
        assert "shout" not in text.lower()
        assert "softly" in text.lower()

    def test_exclamation_not_treated_as_angry(self):
        from webapp.conversation_service import ConversationService

        class _Svc(ConversationService):
            def __init__(self):
                pass

        svc = _Svc()
        assert svc._detect_emotion_from_text("Please! You have to be quiet!") == "calm"
        assert svc._detect_emotion_from_text("She is angry at you.") == "angry"

    def test_create_voice_stores_accent(self, provider, monkeypatch):
        monkeypatch.setattr(provider, "_try_cosyvoice_init", lambda: False)
        monkeypatch.setattr(provider, "_try_openvoice_fallback", lambda: False)
        provider._backend_ready = True
        provider._loaded = True
        provider._fallback_mode = False
        monkeypatch.setattr(
            provider,
            "_default_reference_audio",
            lambda: str(provider.cache_dir / "ref.wav"),
        )
        (provider.cache_dir / "ref.wav").write_bytes(b"RIFF")
        config = provider.create_voice(
            "rose",
            "ghost girl",
            reference_audio=str(provider.cache_dir / "ref.wav"),
            accent="british",
            language="en",
        )
        assert config.extra_metadata.get("accent") == "british"
        assert config.extra_metadata.get("language") == "en"
        assert provider._resolve_voice_accent(config) == "british"
        assert config.extra_metadata.get("prompt_text") == CosyVoiceProvider.DEFAULT_PROMPT_TEXT

    def test_english_cross_lingual_text_keeps_spoken_line_after_endofprompt(self, provider):
        provider._model_version = "v3"
        instruct = provider._format_cosyvoice3_instruct(
            "calm", accent="eastern european", language="en"
        )
        spoken = "I speak these words slowly and clearly, just as I always speak."
        tagged = provider._cross_lingual_tts_text(spoken, instruct)
        preamble, line = tagged.split("<|endofprompt|>", 1)
        assert preamble.startswith("You are a helpful assistant.")
        assert "Eastern European" in preamble
        assert line == spoken

    def test_english_zero_shot_instruct_is_short(self, provider):
        text = provider._english_zero_shot_instruct("eastern european", "en")
        assert text.startswith("You are a helpful assistant.")
        assert "Eastern European" in text
        assert "vampire" not in text.lower()
        assert len(text) < 180
        instruct = provider._format_cosyvoice3_instruct(
            "calm", accent="eastern european", language="en"
        )
        zs = provider._zero_shot_prompt_text(instruct, CosyVoiceProvider.DEFAULT_PROMPT_TEXT)
        preamble, transcript = zs.split("<|endofprompt|>", 1)
        assert "Eastern European" in preamble
        assert transcript == CosyVoiceProvider.DEFAULT_PROMPT_TEXT

    def test_english_invoke_uses_zero_shot_not_instruct2(self, provider, monkeypatch):
        provider._model_version = "v3"
        calls = []
        monkeypatch.setattr(
            provider,
            "_bundled_zero_shot_prompt_wav",
            lambda: "/tmp/zero_shot_prompt.wav",
        )

        class FakeCosy:
            def inference_cross_lingual(self, text, prompt_wav, stream=False):
                calls.append(("cross_lingual", text, prompt_wav, stream))
                return iter(())

            def inference_instruct2(self, text, instruct_text, prompt_wav, stream=False):
                calls.append(("instruct2", text, instruct_text, prompt_wav, stream))
                return iter(())

            def inference_zero_shot(self, text, prompt_text, prompt_wav, stream=False):
                calls.append(("zero_shot", text, prompt_text, prompt_wav, stream))
                return iter(())

        provider._cosyvoice = FakeCosy()
        instruct = provider._english_zero_shot_instruct("british", "en")
        spoken = "Good afternoon. Welcome to the garden party."
        list(
            provider._invoke_cosyvoice(
                text=spoken,
                prompt_wav="/tmp/cross_lingual_prompt.wav",
                prompt_text=CosyVoiceProvider.DEFAULT_PROMPT_TEXT,
                instruct_text=instruct,
                language="en",
                accent="british",
                stream=False,
                use_style_instruct=True,
            )
        )
        assert calls and calls[0][0] == "zero_shot"
        assert calls[0][1] == spoken
        assert CosyVoiceProvider.DEFAULT_PROMPT_TEXT in calls[0][2]
        assert "British English" in calls[0][2]
        assert "vampire" not in calls[0][2].lower()
        assert len(calls[0][2]) < 250
        assert calls[0][3] == "/tmp/zero_shot_prompt.wav"
        assert not any(name == "instruct2" for name, *_ in calls)

    def test_chinese_invoke_uses_instruct2(self, provider):
        provider._model_version = "v3"
        calls = []

        class FakeCosy:
            def inference_cross_lingual(self, text, prompt_wav, stream=False):
                calls.append(("cross_lingual", text))
                return iter(())

            def inference_instruct2(self, text, instruct_text, prompt_wav, stream=False):
                calls.append(("instruct2", text, instruct_text))
                return iter(())

        provider._cosyvoice = FakeCosy()
        list(
            provider._invoke_cosyvoice(
                text="你好",
                prompt_wav="/tmp/zero_shot_prompt.wav",
                prompt_text=CosyVoiceProvider.DEFAULT_PROMPT_TEXT,
                instruct_text="You are a helpful assistant. 请用广东话表达。<|endofprompt|>",
                language="zh",
                accent="cantonese",
                stream=False,
                use_style_instruct=True,
            )
        )
        assert calls and calls[0][0] == "instruct2"


class TestCosyVoiceProviderFallback:
    """Tests for CosyVoiceProvider fallback behavior."""

    @pytest.fixture
    def provider(self, tmp_path):
        cache_dir = str(tmp_path / "tts_test")
        return CosyVoiceProvider(cache_dir=cache_dir, device="cpu")

    def test_initialization_without_cosyvoice(self, provider, monkeypatch):
        """Test initialization when CosyVoice/OpenVoice backends are unavailable."""
        # Never load the real CosyVoice model in unit tests (CPU init is slow).
        monkeypatch.setattr(provider, "_try_cosyvoice_init", lambda: False)
        monkeypatch.setattr(provider, "_try_openvoice_fallback", lambda: False)
        provider.initialize()
        assert provider._loaded is True
        assert provider.is_ready() is False

    def test_status_shows_fallback_mode(self, provider, monkeypatch):
        """Test status shows fallback/unavailable state without loading models."""
        monkeypatch.setattr(provider, "_try_cosyvoice_init", lambda: False)
        monkeypatch.setattr(provider, "_try_openvoice_fallback", lambda: False)
        provider.initialize()
        status = provider.status

        assert "fallback_mode" in status
        assert "loaded" in status
        assert status["provider"] == "cosyvoice"
        assert status["loaded"] is True


# =====================================================================
# Integration Tests
# =====================================================================


class TestCosyVoiceProviderIntegration:
    """Integration tests simulating real usage patterns."""

    @pytest.fixture
    def provider(self, tmp_path):
        cache_dir = str(tmp_path / "tts_integration")
        return MockCosyVoiceProvider(cache_dir=cache_dir)

    def test_npc_voice_pipeline(self, provider):
        """Test complete NPC voice pipeline: create -> generate -> persist -> restore."""
        npc_uid = "npc_goblin_elder"
        voice_prompt = "Ancient goblin with a raspy, wheezy voice"
        dialogue = "Ye shall not pass, ye meddlin' fools!"

        # 1. Create voice
        config = provider.create_voice(npc_uid, voice_prompt)
        assert config.style == "lazy"

        # 2. Generate audio
        audio_path = provider.generate(dialogue, npc_uid, emotion="angry")
        assert os.path.exists(audio_path)

        # 3. Persist voice
        voice_data = provider.persist_voice(npc_uid)
        assert voice_data["npc_uid"] == npc_uid

        # 4. Simulate reload: remove and restore
        provider.remove_voice(npc_uid)
        assert npc_uid not in provider.list_locked_voices()

        restored = provider.restore_voice(npc_uid, voice_data)
        assert restored.npc_uid == npc_uid

        # 5. Generate again with restored voice
        audio_path_2 = provider.generate(dialogue, npc_uid)
        assert os.path.exists(audio_path_2)

    def test_multiple_npcs_different_emotions(self, provider):
        """Test generating the same text with different emotions."""
        npc_uid = "npc_dramatic_actor"
        provider.create_voice(npc_uid, "Versatile actor with clear voice")

        text = "To be or not to be, that is the question"

        # Generate with different emotions
        paths = {}
        for emotion in ["angry", "sad", "cheerful", "whisper", None]:
            path = provider.generate(text, npc_uid, emotion=emotion)
            paths[emotion] = path
            assert os.path.exists(path)

        # All paths should be unique
        assert len(paths) == len(set(paths.values()))

    def test_voice_persistence_across_simulated_reloads(self, provider):
        """Test that voice persistence survives simulated app reloads."""
        npc_uid = "npc_persistent"
        voice_prompt = "Persistent voice that survives reloads"

        # First "session"
        config1 = provider.create_voice(npc_uid, voice_prompt)
        voice_data = provider.persist_voice(npc_uid)

        # Simulate reload: create new provider instance
        new_provider = MockCosyVoiceProvider(cache_dir=str(provider.cache_dir))

        # Restore voice
        new_provider.restore_voice(npc_uid, voice_data)

        # Generate should work
        audio_path = new_provider.generate("Hello after reload", npc_uid)
        assert os.path.exists(audio_path)


# =====================================================================
# Conftest Helper (inline)
# =====================================================================


@pytest.fixture
def tmp_path_fixture(tmp_path):
    """Helper fixture for creating temporary paths."""
    return tmp_path
