"""
TTS wiring for conversation_service.py NPC speech.

This module provides a mixin/patch that adds TTS audio generation
to NPC replies in the conversation flow. It is designed to be
imported by conversation_service.py without modifying its core logic.

Usage in conversation_service.py:
    from webapp.tts.conversation_wiring import TTSConversationMixin
    
    class ConversationService(TTSConversationMixin, ...):
        pass
    
    # OR patch an existing instance:
    tts_wiring = TTSConversationMixin()
    tts_wiring.attach(conversation_service_instance)
"""

import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webapp.conversation_service import ConversationService


class TTSConversationMixin:
    """
    Mixin that adds TTS audio generation to NPC conversation replies.
    
    When an NPC generates a reply, this mixin:
    1. Checks if the NPC has a locked voice
    2. Generates audio using the TTS provider
    3. Adds the audio_url to the conversation payload
    4. Emits a SocketIO 'npc_speech' event with both text and audio
    """
    
    # Emotion keywords to detect from NPC response text
    EMOTION_DETECTION_PATTERNS = {
        "shouting": ["SHOUT", "SHRIEK", "scream", "roar", "bellows"],
        "angry": ["angry", "furious", "shouting", "yelling", "grr", "enraged"],
        "whisper": ["[whisper]", "whisper", "quietly", "softly", "hushed", "[hushed]"],
        "laughing": ["[laughs]", "laughs", "laugh", "chuckle", "giggle", "[chuckles]"],
        "sad": ["[sighs]", "sighs", "sigh", "sad", "mournful", "[mourns]"],
        "fearful": ["afraid", "terrified", "fearful", "scared", "trembling"],
    }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tts_manager = None
        self._tts_attached = False
    
    def attach_tts_manager(self, tts_manager) -> None:
        """
        Attach a TTSManager instance to this conversation service.
        
        Call this during app startup after both the conversation service
        and TTS manager are initialized.
        """
        self._tts_manager = tts_manager
    
    def _detect_emotion_from_text(self, text: str) -> str:
        """
        Detect emotion from NPC response text for TTS style control.
        
        Returns the detected emotion key, or "calm" if no emotion detected.
        """
        if not text:
            return "calm"
        
        text_lower = text.lower()
        
        # Check patterns in priority order
        emotion_priority = ["shouting", "angry", "laughing", "whisper", "sad"]
        
        for emotion, keywords in self.EMOTION_DETECTION_PATTERNS.items():
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    return emotion
        
        return "calm"
    
    def _get_npc_voice_prompt(self, entity) -> str:
        """
        Get the voice prompt for an NPC entity.
        
        Checks the entity's YAML config for a voice.prompt field.
        Falls back to a generic prompt if none is configured.
        """
        # Try to get voice config from entity
        voice_config = getattr(entity, 'voice_config', None)
        if voice_config and isinstance(voice_config, dict):
            prompt = voice_config.get('prompt') or voice_config.get('voice_prompt')
            if prompt:
                return prompt
        
        # Try entity description/backstory for voice hints
        backstory = getattr(entity, 'backstory', None)
        if backstory:
            try:
                bs = backstory() if callable(backstory) else backstory
                if bs:
                    # Extract first sentence as potential voice descriptor
                    import re
                    sentences = re.split(r'[.!?]+', bs)
                    if sentences:
                        return sentences[0].strip()[:100]
            except Exception:
                pass
        
        # Default fallback
        return f"NPC voice for {getattr(entity, 'label', lambda: 'Unknown')()}"
    
    def _generate_tts_audio(self, entity, text: str, emotion: str = None, instruct: str = None) -> str:
        """
        Generate TTS audio for an NPC response.
        
        Returns the audio file path, or empty string if TTS is unavailable.
        """
        if not self._tts_manager or not self._tts_manager.is_enabled():
            return ""
        
        npc_uid = getattr(entity, 'entity_uid', None)
        if not npc_uid:
            return ""
        
        try:
            resolved_emotion = (emotion or '').strip() or self._detect_emotion_from_text(text)
            generate_kwargs = {
                'text': text,
                'npc_uid': npc_uid,
                'emotion': resolved_emotion,
            }
            if instruct:
                generate_kwargs['instruct'] = instruct

            try:
                return self._tts_manager.generate(**generate_kwargs)
            except TypeError:
                generate_kwargs.pop('instruct', None)
                return self._tts_manager.generate(**generate_kwargs)
            
        except ValueError as e:
            # Voice not locked - register prompt and retry
            if "No voice locked" in str(e):
                voice_prompt = self._get_npc_voice_prompt(entity)
                self._tts_manager.register_voice_prompt(npc_uid, voice_prompt)
                try:
                    try:
                        self._tts_manager.create_voice(npc_uid=npc_uid, voice_prompt=voice_prompt)
                    except Exception:
                        pass
                    return self._tts_manager.generate(**generate_kwargs)
                except Exception:
                    return ""
            return ""
        except Exception as e:
            # Log TTS failure but don't break conversation
            if hasattr(self, 'logger'):
                self.logger.warning(
                    "[TTS] Failed to generate audio for %s: %s",
                    getattr(entity, 'label', lambda: 'Unknown')(),
                    e,
                )
            return ""
    
    def emit_tts_event(
        self,
        entity,
        text: str,
        audio_path: str,
        targets=None,
        volume: str = "normal",
        distance_ft: float = None,
        language: str = "common",
        visual_only_usernames: set = None,
        narrative: list = None,
    ) -> None:
        """
        Emit both the conversation payload and TTS audio event.
        
        This replaces the standard emit_conversation call when TTS is enabled.
        """
        # Build the conversation payload as usual
        payload = self.conversation_payload(
            entity,
            text,
            targets=targets or [],
            volume=volume,
            distance_ft=distance_ft,
            language=language,
            visual_only_usernames=visual_only_usernames or set(),
            narrative=narrative or [],
        )
        
        # Add TTS audio URL if generated
        if audio_path:
            import os
            audio_filename = os.path.basename(audio_path)
            payload['audio_url'] = f"/tts/{audio_filename}"
            payload['tts_generated'] = True
        else:
            payload['tts_generated'] = False
        
        # Get recipient usernames
        if hasattr(self, 'conversation_audience_usernames'):
            try:
                response_convs = getattr(entity, '_last_conversation_result', None)
                if not response_convs:
                    # Use the same targets for audience
                    response_convs = [(t, text, [t]) for t in (targets or [])]
                recipient_usernames = self.conversation_audience_usernames(
                    entity, response_convs, targets=targets or []
                )
            except Exception:
                recipient_usernames = set()
        else:
            recipient_usernames = set()
        
        # Emit conversation payload
        if hasattr(self, 'emit_conversation_to_usernames'):
            self.emit_conversation_to_usernames(
                recipient_usernames | (visual_only_usernames or set()),
                payload,
                source_entity=entity,
            )
        
        # Emit TTS audio event via SocketIO (separate from conversation)
        if audio_path and hasattr(self, 'socketio'):
            import os
            audio_filename = os.path.basename(audio_path)
            self.socketio.emit(
                'npc_speech',
                {
                    'audio_url': f"/tts/{audio_filename}",
                    'npc_name': getattr(entity, 'label', lambda: 'NPC')(),
                    'npc_uid': getattr(entity, 'entity_uid', ''),
                    'emotion': self._detect_emotion_from_text(text),
                    'timestamp': time.time(),
                },
            )
