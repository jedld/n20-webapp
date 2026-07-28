"""
OpenVoice TTS provider for natural_20.py NPC voice generation.

Uses OpenVoice (https://github.com/myshell-ai/OpenVoice) for:
- Voice cloning from reference audio (3-second samples)
- Style/emotion control via style codes
- Fast generation (~1s on GPU)

If OpenVoice checkpoints are unavailable, falls back to gTTS for basic speech.

License: MIT (OpenVoice)
"""

import hashlib
import os
import uuid
from pathlib import Path
from typing import Optional

from webapp.tts.base import TTSEngine, VoiceConfig

# Hugging Face repo for OpenVoice V2 checkpoints
OPENVOICE_V2_REPO_ID = "myshell-ai/OpenVoiceV2"

import warnings

# Suppress deprecated resume_download warning (huggingface_hub >= 0.20)
warnings.filterwarnings("ignore", message=".*resume_download.*")


class OpenVoiceTTSProvider(TTSEngine):
    """
    TTS provider using OpenVoice for NPC voice generation.
    
    If OpenVoice checkpoints are unavailable, falls back to gTTS
    which provides basic TTS without voice cloning.
    """
    
    # Emotion/style mapping for D&D roleplay
    EMOTION_STYLES = {
        # Combat / aggressive
        "angry": "angry",
        "yelling": "shouting",
        "shouting": "shouting",
        "furious": "shouting",
        "hostile": "angry",
        
        # Vocal delivery
        "whisper": "whisper",
        "quiet": "whisper",
        "hiding": "whisper",
        
        # Positive emotions
        "cheerful": "cheerful",
        "happy": "cheerful",
        "excited": "cheerful",
        "joyful": "cheerful",
        "laughing": "cheerful",
        
        # Negative emotions
        "sad": "sad",
        "miserable": "sad",
        "sorrowful": "sad",
        
        # Calm / neutral
        "calm": "calm",
        "neutral": "calm",
        "measured": "calm",
        "composed": "calm",
        "fearful": "fearful",
        "scared": "fearful",
        "terrified": "fearful",
        
        # Speech patterns
        "lazy": "lazy",
        "drawling": "lazy",
        "mumbling": "lazy",
        "slurred": "lazy",
    }
    
    def __init__(
        self,
        device: str = "cpu",
        openvoice_dir: Optional[str] = None,
        cache_dir: str = "/tmp/natural20_tts",
    ):
        """
        Initialize the OpenVoice TTS provider.
        
        Args:
            device: "cuda:0" for GPU acceleration, "cpu" for CPU-only
            openvoice_dir: Path to OpenVoice installation. 
                           If None, assumes it's importable via Python.
            cache_dir: Directory for cached voice embeddings and generated audio
        """
        self.device = device
        self.openvoice_dir = openvoice_dir
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self._voice_registry: dict[str, VoiceConfig] = {}
        self._loaded = False
        self._fallback_mode = False
        self._gtts_available = False
    
    def initialize(self) -> None:
        """Initialize TTS. Tries OpenVoice first, falls back to gTTS."""
        if self._loaded:
            return
        
        # Try to initialize OpenVoice
        openvoice_success = self._try_openvoice_init()
        
        if not openvoice_success:
            # Fall back to gTTS
            print("[TTS] OpenVoice not available, falling back to gTTS")
            self._try_gtts_init()
        
        self._loaded = True
    
    def _try_openvoice_init(self) -> bool:
        """Try to initialize OpenVoice. Returns True if successful."""
        try:
            from openvoice.api import BaseSpeakerTTS
        except ImportError:
            print("[TTS] OpenVoice package not installed")
            self._fallback_mode = True
            return False
        
        # Find or download checkpoints
        config_path, checkpoint_path = self._find_or_download_checkpoints()
        
        if not config_path or not checkpoint_path:
            print("[TTS] OpenVoice checkpoints not found, using gTTS fallback")
            self._fallback_mode = True
            return False
        
        try:
            self._base_tts = BaseSpeakerTTS(
                config_path=config_path,
                device=self.device,
            )
            self._base_tts.load_ckpt(checkpoint_path)
            print(f"[TTS] OpenVoice initialized with checkpoint: {checkpoint_path}")
            return True
        except Exception as e:
            print(f"[TTS] OpenVoice initialization failed: {e}, using gTTS fallback")
            self._fallback_mode = True
            return False
    
    def _try_gtts_init(self) -> bool:
        """Try to initialize gTTS fallback."""
        try:
            from gtts import gTTS
            self._gtts_available = True
            print("[TTS] gTTS fallback initialized")
            return True
        except ImportError:
            print("[TTS] gTTS not available. Install with: pip install gtts")
            return False
    
    def _find_or_download_checkpoints(self):
        """Find existing checkpoints or attempt to download them from Hugging Face."""
        # Search for existing checkpoints (V1 structure)
        search_dirs = [
            Path(self.openvoice_dir) / "checkpoints" if self.openvoice_dir else None,
            Path(__file__).parent.parent.parent / ".local_packages" / "OpenVoice" / "checkpoints",
        ]
        
        for search_dir in search_dirs:
            if search_dir and search_dir.exists():
                for pth_file in search_dir.rglob("*.pth"):
                    config_path = pth_file.parent / "config.yaml"
                    if not config_path.exists():
                        config_path = search_dir / "config.json"
                    if config_path.exists():
                        return str(config_path), str(pth_file)
        
        # Search for V2 checkpoints (converter/ structure)
        v2_search_dirs = [
            Path(self.openvoice_dir) / "converter" if self.openvoice_dir else None,
            Path(__file__).parent.parent.parent / ".local_packages" / "OpenVoice" / "converter",
        ]
        
        for search_dir in v2_search_dirs:
            if search_dir and search_dir.exists():
                config_path = search_dir / "config.json"
                checkpoint_files = list(search_dir.glob("checkpoint_*.pth")) + list(search_dir.glob("*.pth"))
                for pth_file in checkpoint_files:
                    if "config" not in pth_file.name.lower() and pth_file.suffix == ".pth":
                        if config_path.exists():
                            return str(config_path), str(pth_file)
        
        # Checkpoints not found locally - try downloading from Hugging Face
        hf_cache_dir = self._ensure_hf_cache_dir()
        local_config, local_ckpt = self._try_hf_download(hf_cache_dir)
        if local_config and local_ckpt:
            return local_config, local_ckpt
        
        return None, None
    
    def _ensure_hf_cache_dir(self) -> Path:
        """Ensure the Hugging Face cache directory exists."""
        hf_cache = self.cache_dir / "openvoice_v2_checkpoints"
        hf_cache.mkdir(parents=True, exist_ok=True)
        return hf_cache
    
    def _try_hf_download(self, cache_dir: Path) -> tuple:
        """Try to download checkpoints from Hugging Face. Returns (config_path, checkpoint_path) or (None, None)."""
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            print("[TTS] huggingface_hub not installed. Install with: pip install huggingface_hub")
            return None, None
        
        try:
            print(f"[TTS] Downloading OpenVoice V2 checkpoints from {OPENVOICE_V2_REPO_ID}...")
            
            # Download the entire repo (it's small ~25MB)
            download_path = snapshot_download(
                repo_id=OPENVOICE_V2_REPO_ID,
                cache_dir=str(cache_dir.parent / ".hf_cache"),
                local_dir=str(cache_dir),
                resume_download=True,
            )
            
            download_path = Path(download_path)
            
            # Find V2 checkpoint files
            converter_dir = download_path / "converter"
            if converter_dir.exists():
                config_path = converter_dir / "config.json"
                # Find the main checkpoint file (not config)
                checkpoint_files = [
                    f for f in converter_dir.glob("*.pth")
                    if "config" not in f.name.lower()
                ]
                if checkpoint_files and config_path.exists():
                    checkpoint_path = checkpoint_files[0]
                    print(f"[TTS] Downloaded V2 checkpoints to {converter_dir}")
                    print(f"[TTS]   config: {config_path}")
                    print(f"[TTS]   checkpoint: {checkpoint_path}")
                    return str(config_path), str(checkpoint_path)
            
            print("[TTS] Downloaded repo but V2 checkpoint structure not found")
            return None, None
            
        except Exception as e:
            print(f"[TTS] Hugging Face download failed: {e}")
            return None, None
    
    def list_locked_voices(self) -> list[str]:
        """Return list of NPC UIDs with locked voices."""
        return list(self._voice_registry.keys())
    
    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
    ) -> VoiceConfig:
        """
        Create a voice for an NPC.

        In fallback mode (gTTS), creates a voice config without voice cloning.
        In OpenVoice mode, extracts speaker embedding from reference audio.
        """
        if not self._loaded:
            self.initialize()

        style = self._classify_voice_prompt(voice_prompt)

        se_path = str(self.cache_dir / f"se_{npc_uid}.pth")
        
        if not self._fallback_mode:
            # OpenVoice mode: extract speaker embedding
            try:
                from openvoice import se_extractor
                if reference_audio and os.path.exists(reference_audio):
                    se_extractor.get_se(
                        reference_audio,
                        se_path=se_path,
                        model=self._base_tts,
                    )
                else:
                    # Use a default reference
                    default_ref = self._find_builtin_reference(style)
                    if default_ref and os.path.exists(default_ref):
                        se_extractor.get_se(
                            default_ref,
                            se_path=se_path,
                            model=self._base_tts,
                        )
                    else:
                        Path(se_path).touch()  # Empty placeholder
            except Exception as e:
                print(f"[TTS] Voice creation failed: {e}")
                Path(se_path).touch()  # Create placeholder
        else:
            # gTTS fallback mode - no voice cloning possible
            print(f"[TTS] Creating voice for {npc_uid} (gTTS fallback - no voice cloning)")
            Path(se_path).touch()  # Placeholder
        
        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=se_path,
            style=style,
            prompt_description=voice_prompt,
            reference_audio=reference_audio,
            extra_metadata={
                "accent": accent,
                "language": language,
            },
        )
        
        self._voice_registry[npc_uid] = config
        return config
    
    def _classify_voice_prompt(self, voice_prompt: str) -> str:
        """Classify a voice prompt into a style code."""
        prompt_lower = voice_prompt.lower()
        
        voice_keywords = {
            "rasp": "lazy", "raspy": "lazy", "gruff": "lazy", "rough": "lazy",
            "old": "lazy", "aged": "lazy", "elderly": "lazy", "wheez": "lazy",
            "deep": "angry", "demonic": "angry", "menacing": "angry",
            "soft": "calm", "gentle": "calm", "quiet": "whisper",
            "ghostly": "whisper", "eerie": "whisper", "haunting": "whisper",
            "spooky": "whisper", "mystical": "calm", "ethereal": "whisper",
            "sad": "sad", "depressed": "sad", "fearful": "fearful",
            "scared": "fearful", "anxious": "fearful",
            "cheerful": "cheerful", "happy": "cheerful", "cheery": "cheerful",
            "bright": "cheerful", "energetic": "cheerful",
            "loud": "shouting", "boisterous": "shouting",
            "child": "whisper", "kid": "whisper", "young": "calm",
        }
        
        for keyword, style in voice_keywords.items():
            if keyword in prompt_lower:
                return style
        
        return "calm"
    
    def _find_builtin_reference(self, style: str) -> Optional[str]:
        """Find a built-in reference audio file for a style."""
        resources_dir = Path(__file__).parent.parent.parent / ".local_packages" / "OpenVoice" / "resources"
        if not resources_dir.exists():
            return None
        
        style_files = {
            "calm": ["demo_speaker0.mp3"],
            "angry": ["demo_speaker1.mp3"],
            "lazy": ["demo_speaker2.mp3"],
        }
        
        for filename in style_files.get(style, []):
            filepath = resources_dir / filename
            if filepath.exists():
                return str(filepath)
        
        return None
    
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
        Generate speech audio for text.
        
        In OpenVoice mode: uses voice cloning with speaker embedding.
        In gTTS fallback mode: uses Google TTS without voice cloning.
        """
        if not self._loaded:
            self.initialize()
        
        if self._fallback_mode:
            return self._generate_gtts(text, npc_uid, output_path)
        
        # OpenVoice mode
        if npc_uid not in self._voice_registry:
            raise ValueError(
                f"No voice registered for NPC '{npc_uid}'. "
                f"Call create_voice() first."
            )
        
        voice_config = self._voice_registry[npc_uid]
        style = emotion or voice_config.style
        openvoice_style = self.EMOTION_STYLES.get(style, "calm")
        
        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"tts_{npc_uid}_{audio_id}.wav")
        
        # Generate using OpenVoice
        speaker_id = list(getattr(self, '_speakers', {}).keys())[0] if hasattr(self, '_speakers') else "0"
        
        try:
            self._base_tts.tts(
                text=text,
                output_path=output_path,
                speaker=speaker_id,
                language="English",
                speed=1.0,
            )
        except Exception as e:
            print(f"[TTS] OpenVoice generation failed: {e}, falling back to gTTS")
            self._fallback_mode = True
            return self._generate_gtts(text, npc_uid, output_path)
        
        return output_path
    
    def _generate_gtts(self, text: str, npc_uid: str, output_path: Optional[str] = None) -> str:
        """Generate speech using gTTS fallback."""
        if not self._gtts_available:
            raise RuntimeError(
                "TTS not available. gTTS fallback not initialized. "
                "Install with: pip install gtts"
            )
        
        from gtts import gTTS
        
        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"tts_{npc_uid}_{audio_id}.mp3")
        
        # Detect language from text (simplified)
        language = "en"
        
        # Speed adjustment based on emotion
        speed = 1.0
        if npc_uid in self._voice_registry:
            style = self._voice_registry[npc_uid].style
            if style in ("fast", "excited", "cheerful"):
                speed = 1.2
            elif style in ("lazy", "sad", "calm"):
                speed = 0.8
        
        try:
            tts = gTTS(text=text, lang=language, slow=speed < 1.0)
            tts.save(output_path)
            print(f"[TTS] Generated audio (gTTS): {output_path}")
        except Exception as e:
            raise RuntimeError(f"gTTS generation failed: {e}")
        
        return output_path
    
    def persist_voice(self, npc_uid: str) -> dict:
        """Export voice config for save/load."""
        if npc_uid not in self._voice_registry:
            raise KeyError(f"No voice found for NPC '{npc_uid}'")
        return self._voice_registry[npc_uid].to_dict()
    
    def restore_voice(self, npc_uid: str, voice_data: dict) -> VoiceConfig:
        """Restore a voice config from save data."""
        config = VoiceConfig.from_dict(voice_data)
        self._voice_registry[npc_uid] = config
        return config
    
    def remove_voice(self, npc_uid: str) -> bool:
        """Remove a voice from the registry."""
        if npc_uid in self._voice_registry:
            se_path = self._voice_registry[npc_uid].se_path
            if se_path and os.path.exists(se_path):
                try:
                    os.unlink(se_path)
                except OSError:
                    pass
            del self._voice_registry[npc_uid]
            return True
        return False
    
    @property
    def provider_name(self) -> str:
        return "openvoice"

    def model_profile_key(self) -> str:
        if self._fallback_mode:
            return "openvoice_gtts"
        return "openvoice"
    
    @property
    def status(self) -> dict:
        return {
            "provider": "openvoice",
            "fallback_mode": self._fallback_mode,
            "gtts_available": self._gtts_available,
            "loaded": self._loaded,
            "device": self.device,
            "voices": len(self._voice_registry),
            "cache_dir": str(self.cache_dir),
        }
