"""
TTS (Text-to-Speech) providers for natural_20.py NPC voice generation.

Supports multiple backends with a common interface:
- CosyVoiceProvider: Zero-shot voice cloning with emotion control (primary)
- MockCosyVoiceProvider: Mock provider for testing
- OpenVoiceTTSProvider: Style-controlled voice synthesis with emotion codes (fallback)

Usage:
    from webapp.tts import TTSManager
    
    manager = TTSManager()
    manager.initialize(provider="cosyvoice", device="cuda")
    
    # Create a voice for an NPC (requires reference audio for CosyVoice)
    manager.create_voice(
        npc_uid="npc_goblin_001",
        voice_prompt="Greedy goblin with a raspy whisper",
        reference_audio="/path/to/reference.wav"
    )
    
    # Generate speech with emotion
    audio_path = manager.generate(
        text="Ye shall not pass, ye meddlin' fools!",
        npc_uid="npc_goblin_001",
        emotion="angry"
    )

Environment Variables:
    TTS_PROVIDER: Provider name ("cosyvoice", "mock_cosyvoice", "openvoice")
    TTS_DEVICE: Device ("cuda" or "cpu")
    TTS_ENABLED: Enable/disable TTS ("1" or "0")
    N20_TTS_CACHE_DIR: Directory for cached audio files
"""

from webapp.tts.base import TTSEngine, VoiceConfig
from webapp.tts.cosyvoice_provider import CosyVoiceProvider, MockCosyVoiceProvider
from webapp.tts.manager import TTSManager, get_tts_manager, init_tts_manager
from webapp.tts.openvoice_provider import OpenVoiceTTSProvider
from webapp.tts.npc_voice import (
    load_voice_config,
    save_voice_config,
    has_voice_config,
    get_voice_prompt,
    set_voice_prompt,
    clear_voice_config,
    resolve_reference_audio_path,
)

__all__ = [
    # Base
    "TTSEngine",
    "VoiceConfig",
    # Providers
    "CosyVoiceProvider",
    "MockCosyVoiceProvider",
    "OpenVoiceTTSProvider",
    # Manager
    "TTSManager",
    "get_tts_manager",
    "init_tts_manager",
    # NPC voice helpers
    "load_voice_config",
    "save_voice_config",
    "has_voice_config",
    "get_voice_prompt",
    "set_voice_prompt",
    "clear_voice_config",
    "resolve_reference_audio_path",
]
