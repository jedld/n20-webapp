"""
Tests for the TTSManager that coordinates TTS providers.

Tests provider selection, voice registry, and integration with
CosyVoiceProvider and MockCosyVoiceProvider.
"""

import os
from pathlib import Path
from unittest import mock

import pytest

from webapp.tts.base import VoiceConfig
from webapp.tts.cosyvoice_provider import CosyVoiceProvider, MockCosyVoiceProvider
from webapp.tts.manager import TTSManager, get_tts_manager, init_tts_manager


# =====================================================================
# TTSManager Initialization Tests
# =====================================================================


class TestTTSManagerInitialization:
    """Tests for TTSManager initialization."""

    def test_default_initialization(self, tmp_path):
        """Test default TTSManager initialization."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))

        assert manager.device == "cpu"
        assert manager._engine is None
        assert manager._provider_name == "cosyvoice"  # default

    def test_initialize_cosyvoice_provider(self, tmp_path):
        """Test initializing with CosyVoice provider (mock version)."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")

        assert manager._engine is not None
        assert manager._provider_name == "mock_cosyvoice"
        assert isinstance(manager._engine, MockCosyVoiceProvider)

    def test_initialize_openvoice_provider_registered(self, tmp_path):
        """Test that OpenVoice provider is still registered."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))

        # OpenVoice should be in PROVIDERS
        assert "openvoice" in manager.PROVIDERS

    def test_initialize_with_device_override(self, tmp_path):
        """Test device override during initialization."""
        manager = TTSManager(device="cpu", cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice", device="cuda")

        # The engine should have been initialized with cuda
        assert manager._provider_name == "mock_cosyvoice"

    def test_initialize_with_invalid_provider(self, tmp_path):
        """Test initialization with invalid provider raises ValueError."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))

        with pytest.raises(ValueError, match="Unknown TTS provider"):
            manager.initialize(provider="nonexistent_provider")

    def test_available_providers(self):
        """Test that expected providers are registered."""
        expected_providers = {"cosyvoice", "mock_cosyvoice", "openvoice", "qwen3", "mock_qwen3"}
        actual_providers = set(TTSManager.PROVIDERS.keys())

        assert expected_providers.issubset(actual_providers)


# =====================================================================
# TTSManager Voice Management Tests
# =====================================================================


class TestTTSManagerVoiceManagement:
    """Tests for voice creation and management via TTSManager."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create and initialize a TTSManager with mock provider."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")
        return manager

    def test_create_voice(self, manager):
        """Test creating a voice via TTSManager."""
        config = manager.create_voice("npc_test", "Test voice prompt")

        assert isinstance(config, VoiceConfig)
        assert config.npc_uid == "npc_test"
        assert config.prompt_description == "Test voice prompt"

    def test_generate(self, manager):
        """Test generating audio via TTSManager."""
        # First create a voice
        manager.create_voice("npc_test", "Test voice")

        # Then generate
        audio_path = manager.generate("Hello world", "npc_test")

        assert audio_path is not None
        assert audio_path.endswith(".wav")

    def test_generate_without_voice_raises_error(self, manager):
        """Test generating without a registered voice raises ValueError."""
        with pytest.raises(ValueError, match="No voice locked"):
            manager.generate("Hello", "npc_nonexistent")

    def test_generate_with_emotion(self, manager):
        """Test generating audio with emotion override."""
        manager.create_voice("npc_test", "Test voice")

        # Should not raise
        audio_path = manager.generate("Hello!", "npc_test", emotion="angry")
        assert audio_path is not None

    def test_list_locked_voices(self, manager):
        """Test listing locked voices."""
        manager.create_voice("npc_1", "Voice 1")
        manager.create_voice("npc_2", "Voice 2")

        voices = manager.list_locked_voices()
        assert set(voices) == {"npc_1", "npc_2"}

    def test_remove_voice(self, manager):
        """Test removing a voice."""
        manager.create_voice("npc_test", "Test voice")
        assert "npc_test" in manager.list_locked_voices()

        result = manager.remove_voice("npc_test")
        assert result is True
        assert "npc_test" not in manager.list_locked_voices()

    def test_persist_all_voices(self, manager):
        """Test persisting all voices."""
        manager.create_voice("npc_1", "Voice 1")
        manager.create_voice("npc_2", "Voice 2")

        voices_data = manager.persist_all_voices()

        assert "npc_1" in voices_data
        assert "npc_2" in voices_data

    def test_restore_all_voices(self, manager):
        """Test restoring all voices."""
        # Create and persist
        manager.create_voice("npc_test", "Test voice")
        voices_data = manager.persist_all_voices()

        # Remove and restore
        manager.remove_voice("npc_test")
        manager.restore_all_voices(voices_data)

        assert "npc_test" in manager.list_locked_voices()

    def test_register_voice_prompt(self, manager):
        """Test registering a voice prompt for auto-creation."""
        manager.register_voice_prompt("npc_test", "Test voice prompt")
        assert manager._npc_voice_prompts["npc_test"] == "Test voice prompt"

    def test_auto_create_on_first_speech(self, manager):
        """Test auto-creation of voice on first speech."""
        manager.set_auto_create(True)
        manager.register_voice_prompt("npc_auto", "Auto voice")

        # Should auto-create the voice
        audio_path = manager.generate("Hello", "npc_auto")
        assert audio_path is not None
        assert "npc_auto" in manager.list_locked_voices()

    def test_disable_auto_create(self, manager):
        """Test that disabling auto-create raises error."""
        manager.set_auto_create(False)

        with pytest.raises(ValueError, match="No voice locked"):
            manager.generate("Hello", "npc_no_prompt")


# =====================================================================
# TTSManager Status and Configuration Tests
# =====================================================================


class TestTTSManagerStatus:
    """Tests for TTSManager status and configuration."""

    @pytest.fixture
    def manager(self, tmp_path):
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")
        return manager

    def test_is_enabled(self, manager):
        """Test is_enabled returns True when engine is initialized."""
        assert manager.is_enabled() is True

    def test_is_enabled_without_initialization(self, tmp_path):
        """Test is_enabled returns False when not initialized."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        assert manager.is_enabled() is False

    def test_is_enabled_with_env_var(self, tmp_path, monkeypatch):
        """Test is_enabled respects TTS_ENABLED env var."""
        monkeypatch.setenv("TTS_ENABLED", "0")
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")

        # is_enabled checks TTS_ENABLED env var
        assert manager.is_enabled() is False

    def test_get_engine(self, manager):
        """Test get_engine returns the engine."""
        engine = manager.get_engine()
        assert engine is not None
        assert isinstance(engine, MockCosyVoiceProvider)

    def test_get_engine_without_initialization(self, tmp_path):
        """Test get_engine raises RuntimeError when not initialized."""
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))

        with pytest.raises(RuntimeError, match="not initialized"):
            manager.get_engine()

    def test_status_endpoint(self, manager):
        """Test the status blueprint endpoint."""
        bp = manager.create_blueprint()

        # The blueprint should be created successfully
        assert bp is not None


# =====================================================================
# TTSManager Blueprint Tests
# =====================================================================


class TestTTSManagerBlueprint:
    """Tests for TTSManager Flask blueprint creation."""

    @pytest.fixture
    def manager(self, tmp_path):
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")
        return manager

    @pytest.fixture
    def client(self, manager):
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(manager.create_blueprint())
        return app.test_client()

    def test_create_blueprint(self, manager):
        """Test blueprint creation."""
        bp = manager.create_blueprint()
        assert bp is not None
        assert bp.name == "tts"

    def test_status_endpoint(self, client):
        """Test /tts/status endpoint."""
        response = client.get("/tts/status")
        assert response.status_code == 200

        data = response.get_json()
        assert data["enabled"] is True
        assert data["provider"] == "mock_cosyvoice"
        assert "device" in data
        assert "locked_voices" in data

    def test_create_voice_endpoint(self, client):
        """Test /tts/create_voice endpoint."""
        response = client.post(
            "/tts/create_voice",
            json={
                "npc_uid": "npc_test",
                "voice_prompt": "Test voice prompt",
            },
        )
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert data["npc_uid"] == "npc_test"

    def test_create_voice_endpoint_missing_fields(self, client):
        """Test /tts/create_voice with missing fields returns 400."""
        response = client.post(
            "/tts/create_voice",
            json={"npc_uid": "npc_test"},  # missing voice_prompt
        )
        assert response.status_code == 400

    def test_remove_voice_endpoint(self, client):
        """Test /tts/remove_voice endpoint."""
        # Create a voice first
        client.post(
            "/tts/create_voice",
            json={"npc_uid": "npc_test", "voice_prompt": "Test"},
        )

        # Then remove it
        response = client.post(
            "/tts/remove_voice",
            json={"npc_uid": "npc_test"},
        )
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True


# =====================================================================
# TTSManager Singleton Tests
# =====================================================================


class TestTTSManagerSingleton:
    """Tests for TTSManager module-level singleton functions."""

    def test_get_tts_manager_creates_new(self, tmp_path, monkeypatch):
        """Test get_tts_manager creates a new instance if none exists."""
        # Reset singleton
        from webapp.tts import manager as manager_module

        manager_module._default_manager = None

        manager = get_tts_manager()
        assert manager is not None
        assert isinstance(manager, TTSManager)

    def test_init_tts_manager(self, tmp_path, monkeypatch):
        """Test init_tts_manager creates and initializes the manager."""
        # Reset singleton
        from webapp.tts import manager as manager_module

        manager_module._default_manager = None

        manager = init_tts_manager(
            provider="mock_cosyvoice",
            cache_dir=str(tmp_path / "tts"),
        )

        assert manager is not None
        assert manager._provider_name == "mock_cosyvoice"
        assert manager._engine is not None


# =====================================================================
# TTSManager Integration Tests
# =====================================================================


class TestTTSManagerIntegration:
    """Integration tests for TTSManager with realistic workflows."""

    @pytest.fixture
    def manager(self, tmp_path):
        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        manager.initialize(provider="mock_cosyvoice")
        return manager

    def test_dnd_conversation_flow(self, manager):
        """Test a complete D&D conversation flow with TTS."""
        # NPC definitions
        npcs = [
            ("npc_goblin_leader", "Greedy goblin with a raspy whisper"),
            ("npc_ancient_dragon", "Ancient dragon with deep booming voice"),
            ("npc_mysterious_wizard", "Ethereal wizard with soft mystical voice"),
        ]

        # Step 1: Create voices for all NPCs
        for npc_uid, prompt in npcs:
            manager.create_voice(npc_uid, prompt)

        # Step 2: Generate dialogue for each NPC with appropriate emotions
        dialogues = {
            "npc_goblin_leader": ("Ye shall not pass!", "angry"),
            "npc_ancient_dragon": ("I am fire and death.", "fearful"),
            "npc_mysterious_wizard": ("Welcome, traveler.", "calm"),
        }

        audio_paths = {}
        for npc_uid, (text, emotion) in dialogues.items():
            path = manager.generate(text, npc_uid, emotion=emotion)
            audio_paths[npc_uid] = path
            assert path is not None

        # Step 3: Persist all voices for save/load
        voices_data = manager.persist_all_voices()
        assert len(voices_data) == 3

        # Step 4: Simulate game reload - create new manager
        new_manager = TTSManager(cache_dir=str(manager.cache_dir))
        new_manager.initialize(provider="mock_cosyvoice")

        # Step 5: Restore voices
        new_manager.restore_all_voices(voices_data)

        # Step 6: Generate more dialogue with restored voices
        for npc_uid in dialogues:
            path = new_manager.generate("Hello after reload", npc_uid)
            assert path is not None

    def test_voice_prompt_auto_creation_flow(self, manager):
        """Test auto-creation of voices from prompts during conversation."""
        manager.set_auto_create(True)

        # Register voice prompts for NPCs (done during NPC loading)
        manager.register_voice_prompt("npc_spawned_1", "Orc warrior with gruff voice")
        manager.register_voice_prompt("npc_spawned_2", "Elf archer with clear tone")

        # Generate dialogue - voices should be auto-created
        path1 = manager.generate("Attack!", "npc_spawned_1", emotion="angry")
        assert "npc_spawned_1" in manager.list_locked_voices()

        path2 = manager.generate("Take aim", "npc_spawned_2", emotion="calm")
        assert "npc_spawned_2" in manager.list_locked_voices()

    def test_concurrent_voice_operations(self, manager):
        """Test multiple voice operations in sequence."""
        npcs = [f"npc_{i}" for i in range(10)]

        # Create all voices
        for i, npc_uid in enumerate(npcs):
            manager.create_voice(npc_uid, f"Voice {i}")

        assert len(manager.list_locked_voices()) == 10

        # Generate for all
        for npc_uid in npcs:
            path = manager.generate(f"Hello from {npc_uid}", npc_uid)
            assert path is not None

        # Persist all
        voices_data = manager.persist_all_voices()
        assert len(voices_data) == 10

        # Remove half
        for npc_uid in npcs[:5]:
            manager.remove_voice(npc_uid)

        assert len(manager.list_locked_voices()) == 5

        # Restore all
        manager.restore_all_voices(voices_data)
        assert len(manager.list_locked_voices()) == 10


# =====================================================================
# Environment Variable Tests
# =====================================================================


class TestTTSManagerEnvVars:
    """Tests for environment variable handling."""

    def test_tts_provider_env_var(self, tmp_path, monkeypatch):
        """Test TTS_PROVIDER env var is respected."""
        monkeypatch.setenv("TTS_PROVIDER", "mock_cosyvoice")

        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        # Default provider should read from env var
        assert manager._provider_name == "mock_cosyvoice"

    def test_tts_device_env_var(self, tmp_path, monkeypatch):
        """Test TTS_DEVICE env var is respected."""
        monkeypatch.setenv("TTS_DEVICE", "cuda")

        manager = TTSManager(cache_dir=str(tmp_path / "tts"))
        assert manager.device == "cuda"

    def test_cache_dir_env_var(self, tmp_path, monkeypatch):
        """Test N20_TTS_CACHE_DIR env var is respected."""
        custom_cache = str(tmp_path / "custom_cache")
        monkeypatch.setenv("N20_TTS_CACHE_DIR", custom_cache)

        manager = TTSManager()
        assert manager.cache_dir == custom_cache
