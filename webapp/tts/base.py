"""
Base TTS engine interface for natural_20.py.

All TTS providers must implement the TTSEngine abstract base class.
This allows swapping providers without changing the conversation service.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class VoiceConfig:
    """Configuration for a locked NPC voice."""
    
    npc_uid: str
    se_path: str                    # Style embedding / reference path
    style: str                      # Default emotion style
    prompt_description: str         # Original voice prompt
    reference_audio: Optional[str] = None  # Original reference audio (if cloned from file)
    extra_metadata: dict = field(default_factory=dict)  # Provider-specific extra data
    
    def to_dict(self) -> dict:
        """Serialize for YAML save/load."""
        return {
            "npc_uid": self.npc_uid,
            "se_path": self.se_path,
            "style": self.style,
            "prompt_description": self.prompt_description,
            "reference_audio": self.reference_audio,
            "extra_metadata": self.extra_metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "VoiceConfig":
        """Deserialize from YAML save data."""
        return cls(
            npc_uid=data["npc_uid"],
            se_path=data["se_path"],
            style=data["style"],
            prompt_description=data["prompt_description"],
            reference_audio=data.get("reference_audio"),
            extra_metadata=data.get("extra_metadata", {}),
        )


class TTSEngine(ABC):
    """
    Abstract base class for TTS engines.
    
    All concrete providers (OpenVoice, XTTS, etc.) must implement these methods.
    """
    
    # Emotion/style mapping for D&D roleplay
    EMOTION_STYLES: dict[str, str] = {}
    
    @abstractmethod
    def initialize(self) -> None:
        """Lazy-load TTS models. Called before first use."""
        ...
    
    @abstractmethod
    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        """
        Create and lock a voice for an NPC.
        
        Args:
            npc_uid: Unique identifier for the NPC
            voice_prompt: Text description of the voice, e.g.
                         "Old man with a raspy voice"
                         "Young woman, soft and gentle tone"
                         "Deep demonic growl"
            reference_audio: Optional path to audio file for exact cloning.
                           If None, the provider selects from built-in voices/styles.
            accent: Optional speaking accent/dialect hint for CosyVoice instruct
                    (e.g. "american", "british", "romanian").
            language: Optional language code/name (e.g. "en", "english").
        
        Returns:
            VoiceConfig with locked voice path
        """
        ...
    
    @abstractmethod
    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        """
        Generate speech audio for an NPC.
        
        Args:
            text: The dialogue text to speak
            npc_uid: The NPC whose voice to use
            emotion: Optional emotion/style override (e.g., "angry", "whisper")
            output_path: Optional output path. Auto-generated if None.
            instruct: Optional free-form delivery notes for providers that support it.
            volume: Conversation volume (whisper|normal|shout).
        
        Returns:
            Path to the generated audio file
        """
        ...
    
    @abstractmethod
    def persist_voice(self, npc_uid: str) -> dict:
        """Export voice config for save/load."""
        ...
    
    @abstractmethod
    def restore_voice(self, npc_uid: str, voice_data: dict) -> VoiceConfig:
        """Restore a voice config from save data."""
        ...
    
    @abstractmethod
    def list_locked_voices(self) -> list[str]:
        """Return all NPC UIDs that have locked voices."""
        ...
    
    @abstractmethod
    def remove_voice(self, npc_uid: str) -> bool:
        """
        Remove a locked voice for an NPC.
        
        Returns True if the voice was removed, False if not found.
        """
        ...
