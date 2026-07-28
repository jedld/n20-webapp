"""
CosyVoice 3 TTS provider for natural_20.py NPC voice generation.

Uses CosyVoice (https://github.com/FunAudioLLM/CosyVoice) for:
- Zero-shot voice cloning from 3-10s reference audio
- Explicit emotion/style control via emotion tokens
- Ultra-low latency (~150ms TTFT on GPU)
- Cross-lingual support (9 languages, 18 dialects)

Falls back to OpenVoice if CosyVoice is unavailable.

License: MIT (CosyVoice / FunAudioLLM)
"""

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from webapp.tts.base import TTSEngine, VoiceConfig


class CosyVoiceProvider(TTSEngine):
    """
    TTS provider using CosyVoice 3 for NPC voice generation.

    CosyVoice 3 provides:
    - Zero-shot voice cloning from 3-10 second reference audio
    - Explicit emotion control (angry, cheerful, sad, fearful, calm, etc.)
    - Ultra-low latency (~150ms TTFT on GPU)
    - Cross-lingual support across 9 languages

    If CosyVoice is unavailable, falls back to OpenVoice.
    """

    # Emotion/style mapping for D&D roleplay -> CosyVoice emotion tokens
    # CosyVoice uses emotion tokens like "angry", "cheerful", "sad", "fearful", "calm"
    EMOTION_STYLES: dict[str, str] = {
        # Combat / aggressive
        "angry": "angry",
        "yelling": "angry",
        "shouting": "angry",
        "furious": "angry",
        "hostile": "angry",
        "rage": "angry",
        "enraged": "angry",

        # Vocal delivery
        "whisper": "whisper",
        "quiet": "whisper",
        "hiding": "whisper",
        "secretive": "whisper",
        "muttering": "whisper",

        # Positive emotions
        "cheerful": "cheerful",
        "happy": "cheerful",
        "excited": "cheerful",
        "joyful": "cheerful",
        "laughing": "cheerful",
        "gleeful": "cheerful",
        "merry": "cheerful",
        "enthusiastic": "cheerful",

        # Negative emotions
        "sad": "sad",
        "miserable": "sad",
        "sorrowful": "sad",
        "depressed": "sad",
        "grieving": "sad",
        "mourning": "sad",
        "crying": "sad",
        "weeping": "sad",

        # Calm / neutral
        "calm": "calm",
        "neutral": "calm",
        "measured": "calm",
        "composed": "calm",
        "serene": "calm",
        "peaceful": "calm",
        "stoic": "calm",
        "monotone": "calm",
        "formal": "calm",
        "diplomatic": "calm",

        # Fear
        "fearful": "fearful",
        "scared": "fearful",
        "terrified": "fearful",
        "panicked": "fearful",
        "anxious": "fearful",
        "worried": "fearful",
        "dreading": "fearful",

        # Disgust
        "disgusted": "disgusted",
        "contemptuous": "disgusted",
        "sneering": "disgusted",

        # Surprise
        "surprised": "surprised",
        "shocked": "surprised",
        "astonished": "surprised",

        # Speech patterns
        "lazy": "lazy",
        "drawling": "lazy",
        "mumbling": "lazy",
        "slurred": "lazy",
        "stuttering": "nervous",
        "nervous": "nervous",
        "stammering": "nervous",
        "hesitant": "nervous",
        "uncertain": "nervous",
    }

    # Default CosyVoice emotion token (used when no mapping matches)
    DEFAULT_EMOTION = "calm"

    # CosyVoice model identifier (Hugging Face)
    COSYVOICE_MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"

    # Transcript that matches third_party/CosyVoice/asset/zero_shot_prompt.wav
    DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。"
    # CosyVoice3 requires an assistant preamble + <|endofprompt|> marker.
    COSYVOICE3_PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
    ENGLISH_PROMPT_TEXT = "Hello there. How are you doing today?"

    # Map short accent labels -> CosyVoice3 instruct fragments (English fantasy default).
    ACCENT_INSTRUCT: dict[str, str] = {
        "american": "speak in clear American English",
        "us": "speak in clear American English",
        "usa": "speak in clear American English",
        "british": "speak in British English",
        "uk": "speak in British English",
        "rp": "speak in Received Pronunciation British English",
        "english": "speak in clear English",
        "en": "speak in clear English",
        "irish": "speak in English with an Irish accent",
        "scottish": "speak in English with a Scottish accent",
        "australian": "speak in English with an Australian accent",
        "romanian": "speak in English with a Romanian accent",
        "eastern european": "speak in English with an Eastern European accent",
        "eastern_european": "speak in English with an Eastern European accent",
        "transylvanian": "speak in English with a Transylvanian accent",
        "slavic": "speak in English with a Slavic accent",
        "german": "speak in English with a German accent",
        "french": "speak in English with a French accent",
        "spanish": "speak in English with a Spanish accent",
        "italian": "speak in English with an Italian accent",
        "russian": "speak in English with a Russian accent",
        "chinese": "speak in Mandarin Chinese",
        "zh": "speak in Mandarin Chinese",
        "mandarin": "speak in Mandarin Chinese",
        "cantonese": "speak Cantonese",
        "yue": "speak Cantonese",
    }

    def __init__(
        self,
        device: str = "cpu",
        cosyvoice_dir: Optional[str] = None,
        cache_dir: str = "/tmp/natural20_tts",
        model_id: Optional[str] = None,
    ):
        """
        Initialize the CosyVoice TTS provider.

        Args:
            device: "cuda"/"cuda:0"/"gpu" for GPU, "cpu" for CPU-only
            cosyvoice_dir: Path to CosyVoice model directory.
                          If None, looks under cache_dir then downloads.
            cache_dir: Directory for cached voice embeddings and generated audio
            model_id: Hugging Face model ID (defaults to Fun-CosyVoice3-0.5B-2512)
        """
        self.device = self._normalize_device(device)
        self.cosyvoice_dir = cosyvoice_dir
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id or self.COSYVOICE_MODEL_ID

        self._voice_registry: dict[str, VoiceConfig] = {}
        self._loaded = False
        self._fallback_mode = False
        self._openvoice_available = False
        self._backend_ready = False
        self._cosyvoice = None
        self._model_version = None  # "v1" | "v2" | "v3"

    @staticmethod
    def _cuda_device_index(device: str) -> int:
        if device.startswith("cuda:"):
            return int(device.split(":", 1)[1])
        return 0

    @staticmethod
    def _gpu_capability_supported(device_index: int) -> bool:
        import torch

        if not torch.cuda.is_available():
            return False
        if device_index < 0 or device_index >= torch.cuda.device_count():
            return False
        major, minor = torch.cuda.get_device_capability(device_index)
        sm_tag = f"sm_{major}{minor}"
        try:
            arch_list = torch.cuda.get_arch_list()
            if arch_list:
                return sm_tag in arch_list
        except Exception:
            pass
        # Fallback when arch list is unavailable (very old torch builds).
        return major < 12

    @staticmethod
    def _normalize_device(device: Optional[str]) -> str:
        value = (device or "cpu").strip().lower()
        if value in ("gpu", "cuda", "cuda:0"):
            value = "cuda"
        if value == "cuda" or value.startswith("cuda:"):
            try:
                import torch

                idx = CosyVoiceProvider._cuda_device_index(
                    value if value.startswith("cuda:") else "cuda:0"
                )
                if torch.cuda.is_available() and CosyVoiceProvider._gpu_capability_supported(idx):
                    return value if value.startswith("cuda:") else "cuda"
                if torch.cuda.is_available() and idx < torch.cuda.device_count():
                    major, minor = torch.cuda.get_device_capability(idx)
                    print(
                        f"[TTS] CUDA device capability sm_{major}{minor} "
                        "is not supported by this PyTorch build; using CPU."
                    )
            except Exception:
                pass
            return "cpu"
        return "cpu"

    def _format_cosyvoice3_prompt_text(self, prompt_text: str) -> str:
        text = (prompt_text or self.DEFAULT_PROMPT_TEXT).strip()
        if "<|endofprompt|>" in text:
            return text
        return f"{self.COSYVOICE3_PROMPT_PREFIX}{text}"

    def _format_cosyvoice3_instruct(
        self,
        emotion_token: str,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        instruct_override: Optional[str] = None,
        volume: Optional[str] = None,
        voice_prompt: Optional[str] = None,
        voice_profile: Optional[dict] = None,
    ) -> str:
        """Build CosyVoice3 instruct2 text with volume, accent, and LLM delivery notes.

        Volume is always applied: CosyVoice's stock reference tends to sound loud
        unless we explicitly request a soft/whisper delivery.
        """
        from webapp.tts.npc_voice import infer_voice_profile, voice_character_instruct

        bits: list[str] = []
        profile = voice_profile or infer_voice_profile(voice_prompt or '')
        character_bit = voice_character_instruct(profile, voice_prompt or '')
        if character_bit:
            bits.append(f"speak as {character_bit}")
        accent_bit = self._accent_instruct_fragment(accent, language)
        if accent_bit:
            bits.append(accent_bit)

        volume_bit = self._volume_instruct_fragment(volume, emotion_token)
        if volume_bit:
            bits.append(volume_bit)

        override = (instruct_override or "").strip().rstrip(".")
        if override:
            override = re.sub(
                r'(?i)^you are a helpful assistant[.\s]*',
                '',
                override,
            ).strip()
            override = override.replace("<|endofprompt|>", "").strip().rstrip(".")
            # Soften LLM delivery notes that conflict with whisper volume.
            if (volume or "").lower() == "whisper":
                override = re.sub(
                    r'(?i)\b(shout\w*|yell\w*|loud(?:ly)?|bellow\w*|scream\w*)\b',
                    'softly',
                    override,
                )
            if re.match(r'(?i)^(please\s+)?(speak|say|use|deliver|read|whisper|shout)\b', override):
                bits.append(override)
            else:
                bits.append(f"speak with {override}")
        else:
            emotion_bit = self._emotion_instruct_fragment(emotion_token, volume=volume)
            if emotion_bit and emotion_bit not in bits:
                bits.append(emotion_bit)

        if not bits:
            bits.append("speak in a calm tone")

        body = "Please " + " and ".join(bits) + "."
        return f"You are a helpful assistant. {body}<|endofprompt|>"

    def _volume_instruct_fragment(
        self,
        volume: Optional[str],
        emotion_token: Optional[str] = None,
    ) -> str:
        """Map conversation volume to CosyVoice-proven soft/loud instruct phrases."""
        mode = (volume or "").strip().lower()
        emotion = (emotion_token or "").strip().lower()
        if mode == "whisper" or emotion in ("whisper", "quiet", "hushed"):
            # Matches CosyVoice's documented soft-voice instruct examples.
            return "say this in a very soft whispering voice"
        if mode == "shout" or emotion in ("shouting", "yelling"):
            return "say this as loudly as possible"
        return ""

    def _emotion_instruct_fragment(
        self,
        emotion_token: Optional[str],
        volume: Optional[str] = None,
    ) -> str:
        """Map emotion tokens to delivery clauses (volume handled separately)."""
        emotion = (emotion_token or self.DEFAULT_EMOTION).strip().lower() or self.DEFAULT_EMOTION
        if emotion in ("whisper", "quiet", "hushed", "shouting", "yelling"):
            # Intensity already covered by volume fragment.
            return ""
        if emotion == "calm":
            return ""
        if emotion == "fearful":
            if (volume or "").lower() == "whisper":
                return "speak fearfully but remain very quiet"
            return "speak in a fearful tone"
        if emotion == "angry":
            if (volume or "").lower() == "whisper":
                return "speak angrily but in a fierce whisper"
            return "speak in an angry tone"
        if emotion == "sad":
            return "speak in a sad tone"
        if emotion in ("cheerful", "laughing"):
            return "speak in a cheerful tone"
        if emotion in ("nervous", "anxious"):
            return "speak in a nervous tone"
        return f"speak in a {emotion} tone"

    def _accent_instruct_fragment(
        self,
        accent: Optional[str],
        language: Optional[str] = None,
    ) -> str:
        """Translate accent/language labels into a CosyVoice instruct clause."""
        accent_key = (accent or "").strip().lower()
        language_key = (language or "").strip().lower()

        if accent_key in ("", "none", "off", "native", "default"):
            accent_key = ""
        if language_key in ("", "none", "off", "default"):
            language_key = ""

        if accent_key:
            mapped = self.ACCENT_INSTRUCT.get(accent_key)
            if mapped:
                return mapped
            # Free-form accent string from YAML
            if "speak" in accent_key:
                return accent.strip()
            return f"speak in English with a {accent.strip()} accent"

        if language_key in ("en", "english"):
            return "speak in clear American English"
        if language_key in ("zh", "chinese", "mandarin"):
            return "speak in Mandarin Chinese"
        if language_key in ("yue", "cantonese"):
            return "speak Cantonese"
        if language_key:
            return f"speak in {language.strip()}"
        return ""

    @staticmethod
    def _default_accent_from_env() -> Optional[str]:
        """Campaign default accent (English VTT). Set TTS_DEFAULT_ACCENT=none to disable."""
        raw = os.environ.get("TTS_DEFAULT_ACCENT", "american")
        value = (raw or "").strip()
        if value.lower() in ("", "none", "off", "native", "zh", "chinese"):
            return None
        return value

    def _resolve_voice_accent(self, voice_config: VoiceConfig) -> Optional[str]:
        meta = voice_config.extra_metadata or {}
        if "accent" in meta:
            raw = meta.get("accent")
            if raw is None:
                return self._default_accent_from_env()
            value = str(raw).strip()
            if value.lower() in ("", "none", "off", "native"):
                return None
            return value
        return self._default_accent_from_env()

    def _resolve_voice_language(self, voice_config: VoiceConfig) -> Optional[str]:
        meta = voice_config.extra_metadata or {}
        if meta.get("language"):
            return str(meta.get("language")).strip()
        accent = (self._resolve_voice_accent(voice_config) or "").lower()
        if accent in ("zh", "chinese", "mandarin", "cantonese", "yue"):
            return "zh"
        if accent:
            return "en"
        return None

    @staticmethod
    def _ensure_cosyvoice_import_path() -> None:
        """Put local CosyVoice + Matcha-TTS checkouts on sys.path if present."""
        import sys

        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            repo_root / "third_party" / "CosyVoice",
            repo_root / "third_party" / "CosyVoice" / "third_party" / "Matcha-TTS",
        ]
        for path in candidates:
            path_str = str(path)
            if path.is_dir() and path_str not in sys.path:
                sys.path.insert(0, path_str)

    @staticmethod
    def _ensure_real_cv2() -> None:
        """Drop repo sitecustomize cv2 stubs so CosyVoice can import real OpenCV.

        A blank ``types.ModuleType('cv2')`` has ``__spec__ is None`` and crashes
        torchvision / CosyVoice import. Prefer a real ``opencv-python-headless``.
        """
        import sys
        import types

        existing = sys.modules.get("cv2")
        is_stub = (
            existing is not None
            and getattr(existing, "__spec__", None) is None
            and getattr(existing, "__file__", None) is None
            and isinstance(existing, types.ModuleType)
        )
        if is_stub:
            del sys.modules["cv2"]

        try:
            import cv2  # noqa: F401
            if getattr(cv2, "__spec__", None) is None:
                raise ImportError("cv2.__spec__ is None (stub module)")
        except ImportError as exc:
            print(
                f"[TTS] OpenCV (cv2) not available: {exc}. "
                "Install with: pip install opencv-python-headless"
            )
            raise

    def initialize(self) -> None:
        """Initialize TTS. Tries CosyVoice, then OpenVoice, else marks unavailable."""
        if self._loaded:
            return

        cosyvoice_success = self._try_cosyvoice_init()

        if not cosyvoice_success:
            print("[TTS] CosyVoice not available, trying OpenVoice fallback")
            openvoice_success = self._try_openvoice_fallback()
            if openvoice_success:
                self._fallback_mode = True
            else:
                print("[TTS] No speech backend available (CosyVoice/OpenVoice)")
                self._backend_ready = False
                self._loaded = True
                return

        self._backend_ready = True
        self._loaded = True

    def is_ready(self) -> bool:
        """True when a usable speech backend is loaded."""
        return bool(getattr(self, '_backend_ready', False) and self._loaded)

    def _default_reference_audio(self, gender: Optional[str] = None) -> Optional[str]:
        """Bundled CosyVoice sample used when an NPC has no reference clip."""
        gender_key = (gender or '').strip().lower()
        candidates = []
        if gender_key == 'male':
            candidates.extend([
                Path(__file__).resolve().parents[2] / "third_party" / "CosyVoice" / "asset" / "cross_lingual_prompt.wav",
                Path(__file__).resolve().parents[2] / "third_party" / "CosyVoice" / "asset" / "zero_shot_prompt.wav",
            ])
        else:
            candidates.extend([
                Path(__file__).resolve().parents[2] / "third_party" / "CosyVoice" / "asset" / "zero_shot_prompt.wav",
                Path(__file__).resolve().parents[2] / "third_party" / "CosyVoice" / "asset" / "cross_lingual_prompt.wav",
            ])
        candidates.extend([
            self.cache_dir / "Fun-CosyVoice3-0.5B-2512" / "asset" / "zero_shot_prompt.wav",
            Path(__file__).resolve().parents[2] / "third_party" / "CosyVoice" / "asset" / "cross_lingual_prompt.wav",
        ])
        for path in candidates:
            if path.exists():
                return str(path)
        return None

    @staticmethod
    def _patch_torchaudio_io() -> None:
        """Route torchaudio load/save through soundfile.

        TorchAudio 2.9+ delegates to TorchCodec, which needs FFmpeg and extra CUDA
        libs that are often missing in the n20-tts env. CosyVoice only needs basic
        WAV I/O, so soundfile is sufficient and far more reliable.
        """
        try:
            import numpy as np
            import soundfile as sf
            import torch
            import torchaudio
        except ImportError:
            return

        if getattr(torchaudio.load, "_n20_soundfile_patch", False):
            return

        def _load_soundfile(
            uri,
            frame_offset: int = 0,
            num_frames: int = -1,
            normalize: bool = True,
            channels_first: bool = True,
            **_kwargs,
        ):
            data, sample_rate = sf.read(uri, dtype="float32", always_2d=True)
            if frame_offset:
                data = data[frame_offset:]
            if num_frames > 0:
                data = data[:num_frames]
            if not normalize:
                # CosyVoice expects normalized float samples; soundfile already returns them.
                pass
            tensor = torch.from_numpy(np.ascontiguousarray(data.T if channels_first else data))
            return tensor, sample_rate

        def _save_soundfile(
            uri,
            src,
            sample_rate: int,
            channels_first: bool = True,
            **_kwargs,
        ):
            arr = src.detach().cpu().numpy()
            if channels_first and arr.ndim > 1:
                arr = arr.T
            sf.write(uri, arr, sample_rate)

        _load_soundfile._n20_soundfile_patch = True  # type: ignore[attr-defined]
        _save_soundfile._n20_soundfile_patch = True  # type: ignore[attr-defined]
        torchaudio.load = _load_soundfile  # type: ignore[assignment]
        torchaudio.save = _save_soundfile  # type: ignore[assignment]

    def _try_cosyvoice_init(self) -> bool:
        """Try to initialize CosyVoice (v1/v2/v3 via AutoModel). Returns True if successful."""
        try:
            if self.device == "cpu":
                # CosyVoice/torch may still probe CUDA during model load; hide GPUs
                # when we explicitly requested CPU (unsupported GPUs, OOM, etc.).
                os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
            self._ensure_cosyvoice_import_path()
            self._ensure_real_cv2()
            self._patch_torchaudio_io()
            try:
                from cosyvoice.cli.cosyvoice import AutoModel
            except ImportError:
                # Older installs may only expose CosyVoice
                from cosyvoice.cli.cosyvoice import CosyVoice as AutoModel  # type: ignore

            # Determine model path: explicit dir > cache > HuggingFace ID
            model_path = self.cosyvoice_dir
            if not model_path:
                for candidate in [
                    self.cache_dir / "Fun-CosyVoice3-0.5B-2512",
                    self.cache_dir / "CosyVoice-3-0.5B",
                    self.cache_dir / "CosyVoice-300M",
                ]:
                    if candidate.exists():
                        model_path = str(candidate)
                        break

            if not model_path:
                print("[TTS] No local model found. CosyVoice will download from HuggingFace on first use.")
                model_path = self.model_id

            # AutoModel selects CosyVoice / CosyVoice2 / CosyVoice3 from yaml present.
            try:
                self._cosyvoice = AutoModel(model_dir=model_path, load_trt=False, fp16=False)
            except TypeError:
                # CosyVoice v1 constructor uses different kwargs
                self._cosyvoice = AutoModel(model_path)

            class_name = type(self._cosyvoice).__name__
            if class_name == "CosyVoice3":
                self._model_version = "v3"
            elif class_name == "CosyVoice2":
                self._model_version = "v2"
            else:
                self._model_version = "v1"

            print(f"[TTS] CosyVoice initialized ({class_name}) from: {model_path}")
            return True

        except ImportError as e:
            print(f"[TTS] CosyVoice package not installed: {e}")
            print("  Install with: bash scripts/setup_cosyvoice.sh")
            return False
        except Exception as e:
            print(f"[TTS] CosyVoice initialization failed: {e}")
            print("  Make sure models are downloaded and Matcha-TTS is on PYTHONPATH.")
            return False

    def _try_openvoice_fallback(self) -> bool:
        """Try to initialize OpenVoice as fallback."""
        try:
            from openvoice.api import BaseSpeakerTTS

            self._openvoice_available = True

            # Find checkpoints
            config_path, checkpoint_path = self._find_openvoice_checkpoints()

            if not config_path or not checkpoint_path:
                print("[TTS] OpenVoice checkpoints not found")
                return False

            self._base_tts = BaseSpeakerTTS(
                config_path=config_path,
                device=self.device,
            )
            self._base_tts.load_ckpt(checkpoint_path)
            print(f"[TTS] OpenVoice fallback initialized with checkpoint: {checkpoint_path}")
            return True

        except ImportError:
            print("[TTS] OpenVoice not available either")
            return False
        except BaseException as e:
            print(f"[TTS] OpenVoice fallback failed: {e}")
            return False

    def _find_openvoice_checkpoints(self):
        """Find OpenVoice checkpoints in common locations."""
        search_dirs = [
            Path(self.cosyvoice_dir) / "checkpoints" if self.cosyvoice_dir else None,
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

        return None, None

    def list_locked_voices(self) -> list[str]:
        """Return list of NPC UIDs with locked voices."""
        return list(self._voice_registry.keys())

    def _resolve_voice_profile(
        self,
        voice_config: VoiceConfig,
        voice_prompt: Optional[str] = None,
    ) -> dict:
        from webapp.tts.npc_voice import infer_voice_profile

        prompt = voice_prompt or voice_config.prompt_description or ''
        inferred = infer_voice_profile(prompt)
        stored = (voice_config.extra_metadata or {}).get('profile')
        if isinstance(stored, dict) and stored.get('gender'):
            merged = dict(stored)
            if not merged.get('age_band') and inferred.get('age_band'):
                merged['age_band'] = inferred['age_band']
            return merged
        return inferred

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
        Create a voice for an NPC.

        CosyVoice mode: extracts speaker embedding from reference audio.
        OpenVoice fallback mode: extracts speaker embedding from reference audio.

        Args:
            npc_uid: Unique identifier for the NPC
            voice_prompt: Text description of the voice
            reference_audio: Path to audio file for voice cloning (required for CosyVoice)
            accent: Optional accent/dialect label (e.g. american, british, romanian)
            language: Optional language hint (e.g. en, zh)
            voice_metadata: Optional precomputed gender/age profile from NPC YAML

        Returns:
            VoiceConfig with locked voice path

        Raises:
            ValueError: If reference_audio is not provided and no default exists
        """
        if not self._loaded:
            self.initialize()

        style = self._classify_voice_prompt(voice_prompt)

        se_path = str(self.cache_dir / f"se_{npc_uid}.pth")

        if reference_audio and not os.path.exists(reference_audio):
            raise FileNotFoundError(f"Reference audio not found: {reference_audio}")

        from webapp.tts.npc_voice import infer_voice_profile

        profile = voice_metadata or infer_voice_profile(voice_prompt)

        if not reference_audio:
            reference_audio = self._default_reference_audio(profile.get('gender'))

        if reference_audio:
            if not self._fallback_mode:
                # CosyVoice mode: save reference audio path
                se_path = str(self.cache_dir / f"ref_{npc_uid}.wav")
                # Copy reference to cache for consistent access
                import shutil

                shutil.copy2(reference_audio, se_path)
            else:
                # OpenVoice mode: extract speaker embedding
                try:
                    from openvoice import se_extractor

                    se_extractor.get_se(
                        reference_audio,
                        se_path=se_path,
                        model=self._base_tts,
                    )
                except Exception as e:
                    print(f"[TTS] Voice creation failed: {e}")
                    Path(se_path).touch()  # Create placeholder
        else:
            raise ValueError(
                f"CosyVoice requires reference audio for voice cloning. "
                f"Provide a reference_audio file for NPC '{npc_uid}', or install "
                f"third_party/CosyVoice assets (zero_shot_prompt.wav)."
            )

        resolved_language = (language or "").strip() or None
        resolved_accent = accent if accent is not None else None
        # Prefer English prompt transcript when speaking English accents
        accent_key = (resolved_accent or self._default_accent_from_env() or "").lower()
        prompt_text = self.DEFAULT_PROMPT_TEXT
        if accent_key and accent_key not in ("zh", "chinese", "mandarin", "cantonese", "yue"):
            prompt_text = self.ENGLISH_PROMPT_TEXT
        elif (resolved_language or "").lower() in ("en", "english"):
            prompt_text = self.ENGLISH_PROMPT_TEXT

        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=se_path,
            style=style,
            prompt_description=voice_prompt,
            reference_audio=reference_audio,
            extra_metadata={
                "prompt_text": prompt_text,
                "accent": resolved_accent,
                "language": resolved_language,
                "profile": profile,
            },
        )

        self._voice_registry[npc_uid] = config
        return config

    def update_voice_options(
        self,
        npc_uid: str,
        accent: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bool:
        """Update accent/language on an already-locked voice without recloning."""
        config = self._voice_registry.get(npc_uid)
        if not config:
            return False
        if accent is not None:
            config.extra_metadata["accent"] = accent
        if language is not None:
            config.extra_metadata["language"] = language
        accent_key = (self._resolve_voice_accent(config) or "").lower()
        if accent_key and accent_key not in ("zh", "chinese", "mandarin", "cantonese", "yue"):
            config.extra_metadata["prompt_text"] = self.ENGLISH_PROMPT_TEXT
        return True

    def _classify_voice_prompt(self, voice_prompt: str) -> str:
        """Classify a voice prompt into a default emotion style."""
        prompt_lower = voice_prompt.lower()

        voice_keywords = {
            # Lazy / raspy / old
            "rasp": "lazy",
            "raspy": "lazy",
            "gruff": "lazy",
            "rough": "lazy",
            "old": "lazy",
            "aged": "lazy",
            "elderly": "lazy",
            "wheeze": "lazy",
            "crackling": "lazy",
            "cough": "lazy",
            # Angry / menacing
            "deep": "angry",
            "demonic": "angry",
            "menacing": "angry",
            "threatening": "angry",
            "menacing": "angry",
            "intimidating": "angry",
            "ominous": "angry",
            # Soft / gentle
            "soft": "calm",
            "gentle": "calm",
            "quiet": "whisper",
            # Ghostly / eerie
            "ghostly": "whisper",
            "eerie": "whisper",
            "haunting": "whisper",
            "spooky": "whisper",
            "mystical": "calm",
            "ethereal": "whisper",
            "otherworldly": "whisper",
            # Sad
            "sad": "sad",
            "depressed": "sad",
            # Fearful
            "fearful": "fearful",
            "scared": "fearful",
            "anxious": "fearful",
            # Cheerful
            "cheerful": "cheerful",
            "happy": "cheerful",
            "cheery": "cheerful",
            "bright": "cheerful",
            "energetic": "cheerful",
            # Loud
            "loud": "angry",
            "boisterous": "angry",
            "child": "calm",
            "kid": "calm",
            "young": "calm",
            "whimsical": "cheerful",
            "playful": "cheerful",
        }

        for keyword, style in voice_keywords.items():
            if keyword in prompt_lower:
                return style

        return "calm"

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

        CosyVoice mode: uses voice cloning with emotion control.
        OpenVoice fallback mode: uses OpenVoice without emotion control.

        Args:
            text: The dialogue text to speak
            npc_uid: The NPC whose voice to use
            emotion: Optional emotion override (e.g., "angry", "whisper")
            output_path: Optional output path. Auto-generated if None.
            instruct: Optional free-form CosyVoice delivery notes from the NPC LLM
            volume: Conversation volume (whisper|normal|shout) for soft/loud delivery

        Returns:
            Path to the generated audio file

        Raises:
            ValueError: If no voice is registered for the NPC
            RuntimeError: If no TTS backend is available
        """
        if not self._loaded:
            self.initialize()

        if not getattr(self, '_backend_ready', False):
            raise RuntimeError(
                "TTS not available. Neither CosyVoice nor OpenVoice initialized. "
                "Install CosyVoice or set TTS_PROVIDER=mock_cosyvoice."
            )

        if self._fallback_mode:
            return self._generate_openvoice(text, npc_uid, output_path)

        # CosyVoice mode
        if npc_uid not in self._voice_registry:
            raise ValueError(
                f"No voice registered for NPC '{npc_uid}'. "
                f"Call create_voice() first."
            )

        voice_config = self._voice_registry[npc_uid]
        emotion_token = self._resolve_emotion(emotion, voice_config.style)
        accent = self._resolve_voice_accent(voice_config)
        language = self._resolve_voice_language(voice_config)
        # Prefer explicit conversation volume; infer soft/loud from emotion as fallback.
        resolved_volume = (volume or "").strip().lower() or None
        if not resolved_volume and emotion_token == "whisper":
            resolved_volume = "whisper"
        elif not resolved_volume and emotion_token in ("shouting", "angry") and (emotion or "").lower() in ("shouting", "yelling"):
            resolved_volume = "shout"

        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"tts_{npc_uid}_{audio_id}.wav")

        try:
            prompt_wav = voice_config.se_path or voice_config.reference_audio
            if not prompt_wav or not os.path.exists(prompt_wav):
                profile = self._resolve_voice_profile(
                    voice_config,
                    voice_config.prompt_description or '',
                )
                prompt_wav = self._default_reference_audio(profile.get('gender'))
            if not prompt_wav:
                raise RuntimeError("No CosyVoice reference/prompt wav available")

            raw_prompt_text = (
                (voice_config.extra_metadata or {}).get("prompt_text")
                or self.DEFAULT_PROMPT_TEXT
            )
            if self._model_version == "v3":
                prompt_text = self._format_cosyvoice3_prompt_text(raw_prompt_text)
                instruct_text = self._format_cosyvoice3_instruct(
                    emotion_token,
                    accent=accent,
                    language=language,
                    instruct_override=instruct,
                    volume=resolved_volume,
                    voice_prompt=voice_config.prompt_description,
                    voice_profile=self._resolve_voice_profile(
                        voice_config,
                        voice_config.prompt_description,
                    ),
                )
            else:
                prompt_text = raw_prompt_text
                instruct_text = f"Speak in a {emotion_token} tone."
                accent_bit = self._accent_instruct_fragment(accent, language)
                if accent_bit:
                    instruct_text = f"{accent_bit.capitalize()}. {instruct_text}"
                volume_bit = self._volume_instruct_fragment(resolved_volume, emotion_token)
                if volume_bit:
                    instruct_text = f"{volume_bit.capitalize()}. {instruct_text}"
                if instruct:
                    instruct_text = f"{instruct_text} {instruct.strip()}"

            # Prefer instruct2 when accent/language/volume/LLM delivery notes are set.
            results = None
            use_instruct = bool(accent) or bool(instruct) or bool(resolved_volume) or bool(
                language and str(language).lower() not in ("zh", "chinese", "mandarin")
            )
            if (
                use_instruct
                and self._model_version in ("v2", "v3")
                and hasattr(self._cosyvoice, "inference_instruct2")
            ):
                results = self._cosyvoice.inference_instruct2(
                    text,
                    instruct_text,
                    prompt_wav,
                    stream=False,
                )
            elif self._model_version == "v3" and hasattr(self._cosyvoice, "inference_zero_shot"):
                results = self._cosyvoice.inference_zero_shot(
                    text,
                    prompt_text,
                    prompt_wav,
                    stream=False,
                )
            elif self._model_version in ("v2", "v3") and hasattr(self._cosyvoice, "inference_instruct2"):
                results = self._cosyvoice.inference_instruct2(
                    text,
                    instruct_text,
                    prompt_wav,
                    stream=False,
                )
            elif hasattr(self._cosyvoice, "inference_zero_shot"):
                results = self._cosyvoice.inference_zero_shot(
                    text,
                    prompt_text,
                    prompt_wav,
                    stream=False,
                )
            elif hasattr(self._cosyvoice, "inference_instruct"):
                spk_id = (voice_config.extra_metadata or {}).get("spk_id") or "中文女"
                results = self._cosyvoice.inference_instruct(
                    text,
                    spk_id,
                    instruct_text,
                    stream=False,
                )
            else:
                raise RuntimeError("CosyVoice model exposes no supported inference API")

            sample_rate = getattr(self._cosyvoice, "sample_rate", 22050)
            for result in results:
                import torchaudio

                tts_speech = result.get("tts_speech")
                if tts_speech is not None:
                    torchaudio.save(
                        output_path,
                        tts_speech,
                        result.get("tts_speech_freq", sample_rate),
                    )
                    break
            else:
                raise RuntimeError("CosyVoice generated no audio output")

            print(f"[TTS] Generated audio (CosyVoice {self._model_version}): {output_path}")
            return output_path

        except Exception as e:
            print(f"[TTS] CosyVoice generation failed: {e}")
            if self._openvoice_available:
                print("[TTS] Falling back to OpenVoice")
                self._fallback_mode = True
                return self._generate_openvoice(text, npc_uid, output_path)
            # Last resort: emit an audible placeholder so the UI replay path works.
            print("[TTS] Falling back to audible placeholder WAV")
            mock = MockCosyVoiceProvider(device="cpu", cache_dir=str(self.cache_dir))
            mock.initialize()
            if npc_uid not in mock.list_locked_voices():
                mock.create_voice(npc_uid, voice_config.prompt_description or "NPC")
            return mock.generate(text, npc_uid, emotion=emotion, output_path=output_path)

    def generate_stream(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ):
        """Yield (sample_rate, pcm_bytes) while synthesizing speech."""
        from webapp.tts.streaming import iter_wav_pcm_chunks, write_pcm_chunks_to_wav

        if self._fallback_mode or not getattr(self, '_backend_ready', False):
            mock = MockCosyVoiceProvider(device="cpu", cache_dir=str(self.cache_dir))
            mock.initialize()
            if npc_uid not in mock.list_locked_voices():
                voice_config = self._voice_registry.get(npc_uid)
                prompt = voice_config.prompt_description if voice_config else "NPC"
                mock.create_voice(npc_uid, prompt)
            yield from mock.generate_stream(
                text,
                npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
            )
            return

        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"tts_{npc_uid}_{audio_id}.wav")

        try:
            yield from self._generate_stream_cosyvoice(
                text,
                npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
            )
        except Exception as exc:
            print(f"[TTS] CosyVoice streaming failed ({exc}); falling back to buffered stream")
            path = self.generate(
                text,
                npc_uid,
                emotion=emotion,
                output_path=output_path,
                instruct=instruct,
                volume=volume,
            )
            for rate, pcm in iter_wav_pcm_chunks(path):
                yield rate, pcm

    def _generate_stream_cosyvoice(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ):
        """Stream PCM chunks from CosyVoice inference (stream=True)."""
        import numpy as np
        from webapp.tts.streaming import write_pcm_chunks_to_wav

        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'.")

        voice_config = self._voice_registry[npc_uid]
        emotion_token = self._resolve_emotion(emotion, voice_config.style)
        accent = self._resolve_voice_accent(voice_config)
        language = self._resolve_voice_language(voice_config)
        resolved_volume = (volume or "").strip().lower() or None
        if not resolved_volume and emotion_token == "whisper":
            resolved_volume = "whisper"
        elif not resolved_volume and emotion_token in ("shouting", "angry") and (emotion or "").lower() in ("shouting", "yelling"):
            resolved_volume = "shout"

        prompt_wav = voice_config.se_path or voice_config.reference_audio
        if not prompt_wav or not os.path.exists(prompt_wav):
            profile = self._resolve_voice_profile(
                voice_config,
                voice_config.prompt_description or '',
            )
            prompt_wav = self._default_reference_audio(profile.get('gender'))
        if not prompt_wav:
            raise RuntimeError("No CosyVoice reference/prompt wav available")

        raw_prompt_text = (
            (voice_config.extra_metadata or {}).get("prompt_text")
            or self.DEFAULT_PROMPT_TEXT
        )
        if self._model_version == "v3":
            prompt_text = self._format_cosyvoice3_prompt_text(raw_prompt_text)
            instruct_text = self._format_cosyvoice3_instruct(
                emotion_token,
                accent=accent,
                language=language,
                instruct_override=instruct,
                volume=resolved_volume,
                voice_prompt=voice_config.prompt_description,
                voice_profile=self._resolve_voice_profile(
                    voice_config,
                    voice_config.prompt_description,
                ),
            )
        else:
            prompt_text = raw_prompt_text
            instruct_text = f"Speak in a {emotion_token} tone."
            accent_bit = self._accent_instruct_fragment(accent, language)
            if accent_bit:
                instruct_text = f"{accent_bit.capitalize()}. {instruct_text}"
            volume_bit = self._volume_instruct_fragment(resolved_volume, emotion_token)
            if volume_bit:
                instruct_text = f"{volume_bit.capitalize()}. {instruct_text}"
            if instruct:
                instruct_text = f"{instruct_text} {instruct.strip()}"

        use_instruct = bool(accent) or bool(instruct) or bool(resolved_volume) or bool(
            language and str(language).lower() not in ("zh", "chinese", "mandarin")
        )
        if (
            use_instruct
            and self._model_version in ("v2", "v3")
            and hasattr(self._cosyvoice, "inference_instruct2")
        ):
            results = self._cosyvoice.inference_instruct2(
                text, instruct_text, prompt_wav, stream=True,
            )
        elif self._model_version == "v3" and hasattr(self._cosyvoice, "inference_zero_shot"):
            results = self._cosyvoice.inference_zero_shot(
                text, prompt_text, prompt_wav, stream=True,
            )
        elif self._model_version in ("v2", "v3") and hasattr(self._cosyvoice, "inference_instruct2"):
            results = self._cosyvoice.inference_instruct2(
                text, instruct_text, prompt_wav, stream=True,
            )
        elif hasattr(self._cosyvoice, "inference_zero_shot"):
            results = self._cosyvoice.inference_zero_shot(
                text, prompt_text, prompt_wav, stream=True,
            )
        else:
            raise RuntimeError("CosyVoice model exposes no supported streaming inference API")

        sample_rate = getattr(self._cosyvoice, "sample_rate", 22050)
        stored_chunks: list[bytes] = []
        yielded = False
        for result in results:
            speech = result.get("tts_speech")
            if speech is None:
                continue
            rate = int(result.get("tts_speech_freq", sample_rate) or sample_rate)
            sample_rate = rate
            arr = speech.detach().cpu().numpy().astype('float32').flatten()
            pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
            if not pcm:
                continue
            stored_chunks.append(pcm)
            yielded = True
            yield sample_rate, pcm

        if not yielded:
            raise RuntimeError("CosyVoice streaming produced no audio output")
        if output_path and stored_chunks:
            write_pcm_chunks_to_wav(stored_chunks, output_path, sample_rate)
        self._last_stream_output_path = output_path

    def _resolve_emotion(self, emotion: Optional[str], default_style: str) -> str:
        """Resolve an emotion string to a CosyVoice emotion token."""
        if emotion:
            return self.EMOTION_STYLES.get(emotion, self.DEFAULT_EMOTION)
        return self.EMOTION_STYLES.get(default_style, self.DEFAULT_EMOTION)

    def _generate_openvoice(
        self, text: str, npc_uid: str, output_path: Optional[str] = None
    ) -> str:
        """Generate speech using OpenVoice fallback."""
        if not self._openvoice_available:
            raise RuntimeError(
                "TTS not available. Neither CosyVoice nor OpenVoice initialized. "
                "Install with: pip install cosyvoice "
                "or: pip install openvoice-app"
            )

        if npc_uid not in self._voice_registry:
            raise ValueError(
                f"No voice registered for NPC '{npc_uid}'. "
                f"Call create_voice() first."
            )

        voice_config = self._voice_registry[npc_uid]
        style = self._resolve_emotion(None, voice_config.style)

        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"tts_{npc_uid}_{audio_id}.wav")

        speaker_id = (
            list(getattr(self, "_speakers", {}).keys())[0]
            if hasattr(self, "_speakers")
            else "0"
        )

        try:
            self._base_tts.tts(
                text=text,
                output_path=output_path,
                speaker=speaker_id,
                language="English",
                speed=1.0,
            )
            print(f"[TTS] Generated audio (OpenVoice fallback): {output_path}")
            return output_path
        except Exception as e:
            raise RuntimeError(f"OpenVoice generation failed: {e}")

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
        """Remove a voice from the registry and clean up files."""
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
        """Return the provider name."""
        return "cosyvoice"

    def model_profile_key(self) -> str:
        if self._fallback_mode:
            return "cosyvoice_openvoice_fallback"
        version = str(getattr(self, '_model_version', '') or '').strip().lower()
        if version in ('v2', 'v3'):
            return f'cosyvoice_{version}'
        return 'cosyvoice'

    @property
    def status(self) -> dict:
        """Return provider status for debugging."""
        return {
            "provider": "cosyvoice",
            "fallback_mode": self._fallback_mode,
            "openvoice_fallback": self._openvoice_available,
            "loaded": self._loaded,
            "device": self.device,
            "model_id": self.model_id,
            "voices": len(self._voice_registry),
            "cache_dir": str(self.cache_dir),
        }


class MockCosyVoiceProvider(TTSEngine):
    """
    Mock CosyVoice provider for testing without actual model inference.

    Generates placeholder WAV files instead of real TTS output.
    """

    EMOTION_STYLES: dict[str, str] = CosyVoiceProvider.EMOTION_STYLES

    def __init__(
        self,
        device: str = "cpu",
        cache_dir: str = "/tmp/natural20_tts_test",
    ):
        self.device = device
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._voice_registry: dict[str, VoiceConfig] = {}
        self._loaded = True

    def initialize(self) -> None:
        """No-op for mock provider."""
        self._loaded = True
        self._backend_ready = True

    def is_ready(self) -> bool:
        return True

    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        """Create a voice config (mock - no actual voice cloning)."""
        from webapp.tts.npc_voice import infer_voice_profile

        style = self._classify_voice_prompt(voice_prompt)
        profile = voice_metadata or infer_voice_profile(voice_prompt)

        # Create a minimal WAV file as placeholder
        se_path = str(self.cache_dir / f"mock_se_{npc_uid}.wav")
        self._create_placeholder_wav(se_path)

        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=se_path,
            style=style,
            prompt_description=voice_prompt,
            reference_audio=reference_audio,
            extra_metadata={
                "accent": accent,
                "language": language,
                "profile": profile,
            },
        )
        self._voice_registry[npc_uid] = config
        return config

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        """Generate mock audio (placeholder WAV file)."""
        if npc_uid not in self._voice_registry:
            raise ValueError(
                f"No voice registered for NPC '{npc_uid}'. "
                f"Call create_voice() first."
            )

        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"mock_tts_{npc_uid}_{audio_id}.wav")

        # Create a minimal valid WAV file
        self._create_placeholder_wav(output_path)
        return output_path

    def generate_stream(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        chunk_samples: int = 4096,
    ):
        """Yield (sample_rate, pcm_bytes) chunks for streaming playback."""
        if npc_uid not in self._voice_registry:
            raise ValueError(
                f"No voice registered for NPC '{npc_uid}'. "
                f"Call create_voice() first."
            )

        if output_path is None:
            audio_id = uuid.uuid4().hex[:8]
            output_path = str(self.cache_dir / f"mock_tts_{npc_uid}_{audio_id}.wav")

        self._create_placeholder_wav(output_path)
        from webapp.tts.streaming import iter_wav_pcm_chunks

        stored_chunks: list[bytes] = []
        sample_rate = 22050
        for rate, pcm in iter_wav_pcm_chunks(output_path, chunk_samples=chunk_samples):
            sample_rate = rate
            stored_chunks.append(pcm)
            yield rate, pcm
        if stored_chunks:
            from webapp.tts.streaming import write_pcm_chunks_to_wav
            write_pcm_chunks_to_wav(stored_chunks, output_path, sample_rate)
        self._last_stream_output_path = output_path

    def last_stream_output_path(self) -> Optional[str]:
        return getattr(self, '_last_stream_output_path', None)

    def _create_placeholder_wav(self, path: str) -> None:
        """Create a short audible WAV tone (not silence) so mock TTS is hearable."""
        import math
        import struct
        import wave

        sample_rate = 22050
        duration_s = 0.9
        frequency_hz = 440.0
        amplitude = 0.35
        n_frames = int(sample_rate * duration_s)

        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_frames):
                # Soft attack/release envelope so it isn't a harsh click
                t = i / sample_rate
                envelope = 1.0
                attack = 0.04
                release = 0.12
                if t < attack:
                    envelope = t / attack
                elif t > duration_s - release:
                    envelope = max(0.0, (duration_s - t) / release)
                sample = amplitude * envelope * math.sin(2.0 * math.pi * frequency_hz * t)
                frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
            wf.writeframes(bytes(frames))

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

    def list_locked_voices(self) -> list[str]:
        """Return list of NPC UIDs with locked voices."""
        return list(self._voice_registry.keys())

    def remove_voice(self, npc_uid: str) -> bool:
        """Remove a voice from the registry."""
        if npc_uid in self._voice_registry:
            del self._voice_registry[npc_uid]
            return True
        return False

    def _classify_voice_prompt(self, voice_prompt: str) -> str:
        """Classify a voice prompt into a style code."""
        prompt_lower = voice_prompt.lower()

        voice_keywords = {
            # Lazy / raspy / old (longer/more specific keywords first)
            "wheezing": "lazy",
            "wheeze": "lazy",
            "crackling": "lazy",
            "rasp": "lazy",
            "raspy": "lazy",
            "gruff": "lazy",
            "rough": "lazy",
            "old": "lazy",
            "aged": "lazy",
            "elderly": "lazy",
            "cough": "lazy",
            # Angry / menacing
            "threatening": "angry",
            "intimidating": "angry",
            "ominous": "angry",
            "deep": "angry",
            "demonic": "angry",
            "menacing": "angry",
            # Soft / gentle
            "soft": "calm",
            "gentle": "calm",
            "quiet": "whisper",
            # Ghostly / eerie
            "ghostly": "whisper",
            "eerie": "whisper",
            "haunting": "whisper",
            "spooky": "whisper",
            "mystical": "calm",
            "ethereal": "whisper",
            "otherworldly": "whisper",
            # Sad
            "miserable": "sad",
            "depressed": "sad",
            "sad": "sad",
            # Fearful
            "anxious": "fearful",
            "fearful": "fearful",
            "scared": "fearful",
            # Cheerful
            "cheerful": "cheerful",
            "joyful": "cheerful",
            "energetic": "cheerful",
            "cheery": "cheerful",
            "bright": "cheerful",
            "happy": "cheerful",
            # Loud
            "boisterous": "angry",
            "loud": "angry",
            # Other
            "whimsical": "cheerful",
            "playful": "cheerful",
            "child": "calm",
            "kid": "calm",
            "young": "calm",
        }

        for keyword, style in voice_keywords.items():
            if keyword in prompt_lower:
                return style

        return "calm"

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return "mock_cosyvoice"

    def model_profile_key(self) -> str:
        return "mock_cosyvoice"

    @property
    def status(self) -> dict:
        """Return provider status for debugging."""
        return {
            "provider": "mock_cosyvoice",
            "loaded": self._loaded,
            "voices": len(self._voice_registry),
            "cache_dir": str(self.cache_dir),
        }
