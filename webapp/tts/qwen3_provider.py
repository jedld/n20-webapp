"""Qwen3-TTS provider for natural_20.py NPC voice generation.

Supports the open-source Qwen3-TTS family (CustomVoice, VoiceDesign, Base clone)
via the ``qwen-tts`` package. See https://github.com/QwenLM/Qwen3-TTS

Set ``TTS_PROVIDER=qwen3`` (or ``mock_qwen3`` for dev/tests).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from webapp.tts.base import TTSEngine, VoiceConfig

DEFAULT_MODEL_CUSTOM = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
DEFAULT_MODEL_DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_MODEL_BASE = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

CUSTOM_VOICE_SPEAKERS = {
    "female": ("Vivian", "Serena", "Ono_Anna", "Sohee"),
    "male": ("Ryan", "Aiden", "Uncle_Fu", "Dylan", "Eric"),
}

EMOTION_INSTRUCT = {
    "angry": "Speak angrily with forceful, intense delivery.",
    "shouting": "Speak loudly, shouting with high energy.",
    "whisper": "Speak very quietly in a hushed whisper.",
    "calm": "Speak calmly and evenly.",
    "cheerful": "Speak cheerfully with warm, upbeat tone.",
    "sad": "Speak sadly with subdued, sorrowful tone.",
    "fearful": "Speak fearfully, tense and uncertain.",
    "lazy": "Speak slowly with a drawling, relaxed delivery.",
}


def _infer_qwen3_mode(model_id: str) -> str:
    low = (model_id or "").lower()
    if "voicedesign" in low:
        return "voice_design"
    if "base" in low and "custom" not in low:
        return "voice_clone"
    return "custom_voice"


def _pick_speaker(npc_uid: str, gender: Optional[str]) -> str:
    pool = CUSTOM_VOICE_SPEAKERS.get((gender or "").lower(), ())
    if not pool:
        pool = CUSTOM_VOICE_SPEAKERS["male"] + CUSTOM_VOICE_SPEAKERS["female"]
    digest = hashlib.sha256(npc_uid.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(pool)
    return pool[index]


def _resolve_language(language: Optional[str]) -> str:
    if not language:
        return "Auto"
    low = str(language).strip().lower()
    mapping = {
        "en": "English",
        "english": "English",
        "zh": "Chinese",
        "chinese": "Chinese",
        "ja": "Japanese",
        "japanese": "Japanese",
        "ko": "Korean",
        "korean": "Korean",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "ru": "Russian",
    }
    return mapping.get(low, language.strip().title())


def _normalize_tts_device(device: Optional[str]) -> str:
    """Map manager/env device strings to cuda|cpu|cuda:N."""
    value = (device or "cpu").strip().lower()
    if value in ("gpu", "cuda", "cuda:0"):
        return "cuda"
    if value.startswith("cuda:"):
        return value
    return "cpu"


def _torch_device_map(device: str) -> str:
    """Device map for Hugging Face load (always cuda:0 when CUDA_VISIBLE_DEVICES is set)."""
    if device.startswith("cuda"):
        return "cuda:0" if device == "cuda" else device
    return "cpu"


def _model_param_device(model: Any) -> str:
    inner = model
    for attr in ("model", "tts_model", "backbone", "speech_model"):
        candidate = getattr(inner, attr, None)
        if candidate is not None:
            inner = candidate
    try:
        return str(next(inner.parameters()).device)
    except Exception:
        return "unknown"


class Qwen3TTSProvider(TTSEngine):
    """TTS provider using Alibaba Qwen3-TTS models."""

    EMOTION_STYLES = dict(EMOTION_INSTRUCT)

    def __init__(
        self,
        device: str = "cpu",
        model_id: Optional[str] = None,
        cache_dir: str = "/tmp/natural20_tts",
    ):
        self.device = _normalize_tts_device(device)
        self.model_id = model_id or os.environ.get("QWEN3_TTS_MODEL", DEFAULT_MODEL_CUSTOM)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._voice_registry: dict[str, VoiceConfig] = {}
        self._clone_prompts: dict[str, Any] = {}
        self._clone_prompt_keys: dict[str, tuple[str, str, bool]] = {}
        self._model = None
        self._clone_model = None
        self._design_model = None
        self._clone_model_id: Optional[str] = None
        self._design_model_id: Optional[str] = None
        self._loaded = False
        self._backend_ready = False
        self._mode = _infer_qwen3_mode(self.model_id)
        self._last_stream_output_path: Optional[str] = None

    def model_profile_key(self) -> str:
        if self._mode == "voice_clone":
            return "qwen3_voice_clone"
        if self._mode == "voice_design":
            return "qwen3_voice_design"
        return "qwen3_custom_voice"

    def initialize(self) -> None:
        if self._loaded:
            return
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError(
                "qwen-tts package not installed. Run: pip install qwen-tts"
            ) from exc

        clone_only = os.environ.get("N20_TTS_CLONE_ONLY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if clone_only:
            from webapp.tts.voice_baking import clone_model_id

            self.model_id = clone_model_id()

        self._model = self._load_qwen3_model(self.model_id)

        self._mode = _infer_qwen3_mode(self.model_id)
        self._loaded = True
        self._backend_ready = True
        param_device = _model_param_device(self._model)
        gpu_name = ""
        if param_device.startswith("cuda") and torch.cuda.is_available():
            try:
                gpu_name = f", {torch.cuda.get_device_name(0)}"
            except Exception:
                pass
        print(
            f"[TTS] Qwen3-TTS initialized ({self.model_id}, mode={self._mode}, "
            f"device={param_device}{gpu_name})"
        )
        if os.environ.get("N20_TTS_WARMUP", "1").strip().lower() not in ("0", "no", "false"):
            self._warmup_gpu()
        if (
            not clone_only
            and os.environ.get("N20_TTS_PRELOAD_CLONE", "1").strip().lower()
            not in ("0", "no", "false")
        ):
            try:
                self._get_clone_model()
            except Exception as exc:
                print(f"[TTS] Qwen3 clone preload skipped: {exc}")

    def is_ready(self) -> bool:
        return bool(self._backend_ready)

    def _resolve_warmup_ref_audio(self) -> Optional[str]:
        """Resolve optional clone-mode warmup reference (env or cwd-relative path)."""
        raw = os.environ.get("QWEN3_WARMUP_REF_AUDIO", "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if candidate.is_file():
            return str(candidate.resolve())
        webapp_dir = Path(__file__).resolve().parents[1]
        from_webapp = (webapp_dir / raw).resolve()
        if from_webapp.is_file():
            return str(from_webapp)
        return None

    def _warmup_gpu(self) -> None:
        """Compile CUDA kernels once so the first NPC line is not a multi-minute wait."""
        if self._model is None or not self.device.startswith("cuda"):
            return
        try:
            import time

            import torch
            from webapp.tts.voice_baking import bake_sample_text

            t0 = time.perf_counter()
            with torch.inference_mode():
                if self._mode == "voice_design":
                    self._model.generate_voice_design(
                        text="Warmup.",
                        language="English",
                        instruct="Neutral calm voice.",
                    )
                elif self._mode == "voice_clone":
                    ref_audio = self._resolve_warmup_ref_audio()
                    if not ref_audio:
                        print(
                            "[TTS] Qwen3 clone warmup skipped "
                            "(set QWEN3_WARMUP_REF_AUDIO to a baked sample WAV)"
                        )
                        return
                    ref_text = os.environ.get("QWEN3_WARMUP_REF_TEXT", "").strip()
                    if not ref_text:
                        sidecar = Path(ref_audio).with_suffix(".ref.txt")
                        if sidecar.is_file():
                            ref_text = sidecar.read_text(encoding="utf-8").strip()
                    if not ref_text:
                        ref_text = bake_sample_text()
                    prompt = self._model.create_voice_clone_prompt(
                        ref_audio=ref_audio,
                        ref_text=ref_text,
                    )
                    self._model.generate_voice_clone(
                        text="Warmup.",
                        language="English",
                        voice_clone_prompt=prompt,
                        do_sample=False,
                    )
                else:
                    speaker = CUSTOM_VOICE_SPEAKERS["female"][0]
                    self._model.generate_custom_voice(
                        text="Warmup.",
                        language="English",
                        speaker=speaker,
                    )
            elapsed = time.perf_counter() - t0
            print(f"[TTS] Qwen3 GPU warmup complete ({elapsed:.1f}s)")
        except Exception as exc:
            print(f"[TTS] Qwen3 GPU warmup skipped: {exc}")

    def _build_load_kwargs(self) -> dict[str, Any]:
        import torch

        device_map = _torch_device_map(self.device)
        dtype = torch.bfloat16 if device_map.startswith("cuda") else torch.float32
        load_kwargs: dict[str, Any] = {
            "device_map": device_map,
            "dtype": dtype,
        }
        use_flash = os.environ.get("QWEN3_USE_FLASH_ATTN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if use_flash:
            try:
                import flash_attn  # noqa: F401

                load_kwargs["attn_implementation"] = "flash_attention_2"
            except ImportError:
                print(
                    "[TTS] QWEN3_USE_FLASH_ATTN=1 but flash-attn is not installed; "
                    "using SDPA attention instead."
                )
                load_kwargs["attn_implementation"] = "sdpa"
        elif device_map.startswith("cuda"):
            load_kwargs["attn_implementation"] = "sdpa"
        return load_kwargs

    def _load_qwen3_model(self, model_id: str):
        from qwen_tts import Qwen3TTSModel

        load_kwargs = self._build_load_kwargs()
        try:
            return Qwen3TTSModel.from_pretrained(model_id, **load_kwargs)
        except Exception:
            load_kwargs.pop("attn_implementation", None)
            return Qwen3TTSModel.from_pretrained(model_id, **load_kwargs)

    def _get_design_model(self):
        from webapp.tts.voice_baking import design_model_id

        if self._mode == "voice_design" and self._model is not None:
            return self._model
        if self._design_model is not None:
            return self._design_model
        self._design_model_id = design_model_id()
        print(f"[TTS] Loading Qwen3 design model ({self._design_model_id})")
        self._design_model = self._load_qwen3_model(self._design_model_id)
        return self._design_model

    def _get_clone_model(self):
        if self._mode == "voice_clone" and self._model is not None:
            return self._model
        if self._clone_model is not None:
            return self._clone_model
        from webapp.tts.voice_baking import clone_model_id

        self._clone_model_id = clone_model_id()
        print(f"[TTS] Loading Qwen3 clone model ({self._clone_model_id})")
        self._clone_model = self._load_qwen3_model(self._clone_model_id)
        return self._clone_model

    def _bake_voice_reference(
        self,
        npc_uid: str,
        design_prompt: str,
        language: str,
        campaign_root: str,
    ) -> tuple[str, str]:
        from webapp.tts.voice_baking import bake_sample_text, load_baked_ref_text, save_baked_ref_text, voice_sample_path

        out = voice_sample_path(campaign_root, npc_uid)
        if out is None:
            raise RuntimeError(f"Cannot bake voice sample without campaign_root for {npc_uid}")
        out.parent.mkdir(parents=True, exist_ok=True)
        ref_text = bake_sample_text()
        if out.is_file() and out.stat().st_size > 44:
            print(f"[TTS] Reusing baked voice sample for {npc_uid}: {out}")
            return str(out), load_baked_ref_text(campaign_root, npc_uid)

        design_model = self._get_design_model()
        print(f"[TTS] Baking voice sample for {npc_uid} → {out}")
        import torch

        with torch.inference_mode():
            wavs, sample_rate = design_model.generate_voice_design(
                text=ref_text,
                language=language,
                instruct=design_prompt,
                do_sample=False,
            )
        self._write_wav(str(out), wavs[0], sample_rate)
        save_baked_ref_text(campaign_root, npc_uid, ref_text)
        print(f"[TTS] Baked voice sample for {npc_uid}")
        return str(out), ref_text

    def bake_voice_sample(
        self,
        npc_uid: str,
        design_prompt: str,
        *,
        language: str = "English",
        campaign_root: str,
    ) -> str:
        """Bake and return the path to a reference WAV for an NPC."""
        if not self._loaded:
            self.initialize()
        path, _ref_text = self._bake_voice_reference(
            npc_uid,
            design_prompt,
            _resolve_language(language),
            campaign_root,
        )
        return path

    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        from webapp.tts.npc_voice import infer_voice_profile
        from webapp.tts.voice_baking import (
            discover_baked_sample,
            should_bake_qwen3_design_voice,
        )

        if not self._loaded:
            self.initialize()

        profile = voice_metadata or infer_voice_profile(voice_prompt)
        gender = (profile.get("gender") or "").lower()
        speaker = _pick_speaker(npc_uid, gender)
        resolved_language = _resolve_language(language or profile.get("language"))

        resolved_strategy = str(profile.get("resolved_strategy") or "").strip().lower()
        profile_strategy = str((profile.get("profile") or {}).get("strategy") or "").strip().lower()
        design_prompt = str(profile.get("design_prompt") or voice_prompt or "").strip()
        campaign_root = str(profile.get("campaign_root") or "").strip() or None
        effective_mode = self._mode
        if resolved_strategy == "design":
            effective_mode = "voice_design"
        elif resolved_strategy == "clone":
            effective_mode = "voice_clone"
        elif resolved_strategy == "preset":
            effective_mode = "custom_voice"

        if not reference_audio and campaign_root:
            from webapp.tts.voice_baking import discover_baked_sample, load_baked_ref_text

            reference_audio = discover_baked_sample(
                campaign_root,
                npc_uid,
                npc_type=str((profile.get("profile") or {}).get("npc_type") or profile.get("npc_type") or "").strip()
                or None,
            )
        baked_ref_text = ""
        if reference_audio and os.path.exists(reference_audio) and campaign_root:
            from webapp.tts.voice_baking import load_baked_ref_text

            baked_ref_text = load_baked_ref_text(campaign_root, npc_uid)
        if reference_audio and os.path.exists(reference_audio):
            effective_mode = "voice_clone"

        se_path = reference_audio
        if not se_path or not os.path.exists(se_path):
            se_path = str(self.cache_dir / f"qwen3_ref_{npc_uid}.wav")

        extra = {
            "qwen3_mode": effective_mode,
            "speaker": speaker,
            "language": resolved_language,
            "voice_prompt": voice_prompt,
            "design_prompt": design_prompt,
            "resolved_strategy": resolved_strategy or effective_mode,
            "accent": accent,
            "profile": profile,
            "ref_text": baked_ref_text or design_prompt[:240] or voice_prompt[:240],
            "campaign_root": campaign_root,
        }

        if should_bake_qwen3_design_voice(
            resolved_strategy=resolved_strategy or effective_mode,
            reference_audio=reference_audio,
            campaign_root=campaign_root,
            npc_uid=npc_uid,
            profile_strategy=profile_strategy,
        ):
            reference_audio, ref_text = self._bake_voice_reference(
                npc_uid,
                design_prompt,
                resolved_language,
                campaign_root,
            )
            effective_mode = "voice_clone"
            extra["qwen3_mode"] = "voice_clone"
            extra["reference_audio"] = reference_audio
            extra["ref_text"] = ref_text
            extra["baked_from_design"] = True
            extra["resolved_strategy"] = "clone"
            se_path = reference_audio

        if effective_mode == "voice_clone" and reference_audio and os.path.exists(reference_audio):
            extra["reference_audio"] = reference_audio
            extra["qwen3_mode"] = "voice_clone"
            self._ensure_clone_prompt(npc_uid, reference_audio, extra.get("ref_text", ""))

        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=se_path,
            style="calm",
            prompt_description=voice_prompt,
            reference_audio=reference_audio,
            extra_metadata=extra,
        )
        self._voice_registry[npc_uid] = config
        return config

    def _ensure_clone_prompt(
        self,
        npc_uid: str,
        ref_audio: str,
        ref_text: str,
    ) -> None:
        model = self._get_clone_model()
        x_vector_only = not bool((ref_text or "").strip())
        prompt_key = (ref_audio, ref_text or "", x_vector_only)
        if self._clone_prompt_keys.get(npc_uid) == prompt_key and npc_uid in self._clone_prompts:
            return
        self._clone_prompt_keys[npc_uid] = prompt_key
        self._clone_prompts[npc_uid] = model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text or "",
            x_vector_only_mode=x_vector_only,
        )

    def _compose_instruct(
        self,
        voice_config: VoiceConfig,
        emotion: Optional[str],
        instruct: Optional[str],
        volume: Optional[str],
    ) -> str:
        meta = voice_config.extra_metadata or {}
        mode = meta.get("qwen3_mode") or self._mode

        # VoiceDesign uses instruct as timbre identity — keep it stable per NPC.
        if mode == "voice_design":
            return str(meta.get("design_prompt") or voice_config.prompt_description or "").strip()

        parts: list[str] = []
        design = str(meta.get("design_prompt") or "").strip()
        if design:
            parts.append(design)
        elif voice_config.prompt_description:
            parts.append(voice_config.prompt_description.strip())
        emotion_key = (emotion or voice_config.style or "").strip().lower()
        if emotion_key and emotion_key in EMOTION_INSTRUCT:
            parts.append(EMOTION_INSTRUCT[emotion_key])
        if instruct and instruct.strip():
            parts.append(instruct.strip())
        vol = (volume or "").strip().lower()
        if vol == "whisper":
            parts.append("Speak in a quiet whisper.")
        elif vol == "shout":
            parts.append("Speak loudly, projecting your voice.")
        accent = (voice_config.extra_metadata or {}).get("accent")
        if accent and str(accent).lower() not in ("", "none", "american"):
            parts.append(f"Use a {accent} accent.")
        return " ".join(p for p in parts if p).strip()

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        if not self._loaded:
            self.initialize()
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")

        if output_path is None:
            output_path = str(
                self.cache_dir / f"qwen3_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )

        voice_config = self._voice_registry[npc_uid]
        meta = voice_config.extra_metadata or {}
        language = _resolve_language(meta.get("language"))
        delivery = self._compose_instruct(voice_config, emotion, instruct, volume)

        wavs, sample_rate = self._synthesize(
            text=text,
            voice_config=voice_config,
            language=language,
            delivery=delivery,
        )
        self._write_wav(output_path, wavs[0], sample_rate)
        return output_path

    def _synthesize(
        self,
        *,
        text: str,
        voice_config: VoiceConfig,
        language: str,
        delivery: str,
    ):
        if self._model is None:
            raise RuntimeError("Qwen3-TTS model not initialized")

        meta = voice_config.extra_metadata or {}
        mode = meta.get("qwen3_mode") or self._mode

        import torch

        with torch.inference_mode():
            if mode == "voice_design":
                design = str(meta.get("design_prompt") or voice_config.prompt_description or "").strip()
                return self._model.generate_voice_design(
                    text=text,
                    language=language,
                    instruct=delivery or design,
                )
            if mode == "voice_clone":
                ref_audio = meta.get("reference_audio") or voice_config.reference_audio
                if ref_audio and os.path.exists(ref_audio):
                    self._ensure_clone_prompt(
                        voice_config.npc_uid,
                        ref_audio,
                        meta.get("ref_text", ""),
                    )
                prompt = self._clone_prompts.get(voice_config.npc_uid)
                if prompt is None:
                    raise RuntimeError(
                        f"No voice clone prompt for NPC '{voice_config.npc_uid}'. "
                        "Provide reference_audio when creating the voice."
                    )
                clone_model = self._get_clone_model()
                return clone_model.generate_voice_clone(
                    text=text,
                    language=language,
                    voice_clone_prompt=prompt,
                    do_sample=False,
                )

            speaker = meta.get("speaker") or _pick_speaker(voice_config.npc_uid, None)
            kwargs: dict[str, Any] = {
                "text": text,
                "language": language,
                "speaker": speaker,
            }
            if delivery:
                kwargs["instruct"] = delivery
            return self._model.generate_custom_voice(**kwargs)

    @staticmethod
    def _write_wav(path: str, waveform, sample_rate: int) -> None:
        import soundfile as sf

        sf.write(path, waveform, sample_rate)

    def generate_stream(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
        chunk_samples: int = 4096,
    ) -> Iterator[tuple[int, bytes]]:
        path = self.generate(
            text,
            npc_uid,
            emotion=emotion,
            output_path=output_path,
            instruct=instruct,
            volume=volume,
        )
        self._last_stream_output_path = path
        from webapp.tts.streaming import iter_wav_pcm_chunks

        for rate, pcm in iter_wav_pcm_chunks(path, chunk_samples=chunk_samples):
            yield rate, pcm

    def last_stream_output_path(self) -> Optional[str]:
        return self._last_stream_output_path

    def persist_voice(self, npc_uid: str) -> dict:
        if npc_uid not in self._voice_registry:
            raise KeyError(f"No voice found for NPC '{npc_uid}'")
        return self._voice_registry[npc_uid].to_dict()

    def restore_voice(self, npc_uid: str, voice_data: dict) -> VoiceConfig:
        config = VoiceConfig.from_dict(voice_data)
        self._voice_registry[npc_uid] = config
        meta = config.extra_metadata or {}
        ref_audio = meta.get("reference_audio") or config.reference_audio
        if ref_audio and os.path.exists(ref_audio):
            self._clone_prompts.pop(npc_uid, None)
            self._ensure_clone_prompt(npc_uid, ref_audio, meta.get("ref_text", ""))
        return config

    def list_locked_voices(self) -> list[str]:
        return list(self._voice_registry.keys())

    def remove_voice(self, npc_uid: str) -> bool:
        self._clone_prompts.pop(npc_uid, None)
        if npc_uid in self._voice_registry:
            del self._voice_registry[npc_uid]
            return True
        return False


class MockQwen3Provider(Qwen3TTSProvider):
    """Mock Qwen3 provider for tests and dev without GPU weights."""

    def __init__(self, device: str = "cpu", cache_dir: str = "/tmp/natural20_tts"):
        self.device = device
        self.model_id = "mock"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._voice_registry = {}
        self._clone_prompts = {}
        self._model = None
        self._clone_model = object()
        self._clone_model_id = "mock-clone"
        self._loaded = True
        self._backend_ready = True
        self._mode = "custom_voice"
        self._last_stream_output_path = None

    def initialize(self) -> None:
        self._loaded = True
        self._backend_ready = True

    def is_ready(self) -> bool:
        return True

    def bake_voice_sample(
        self,
        npc_uid: str,
        design_prompt: str,
        *,
        language: str = "English",
        campaign_root: str,
    ) -> str:
        from webapp.tts.voice_baking import voice_sample_path

        out = voice_sample_path(campaign_root, npc_uid)
        if out is None:
            raise RuntimeError("missing campaign_root")
        out.parent.mkdir(parents=True, exist_ok=True)
        self._create_placeholder_wav(str(out))
        return str(out)

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")
        if output_path is None:
            output_path = str(
                self.cache_dir / f"mock_qwen3_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )
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
    ) -> Iterator[tuple[int, bytes]]:
        path = self.generate(
            text, npc_uid, emotion=emotion, output_path=output_path,
            instruct=instruct, volume=volume,
        )
        self._last_stream_output_path = path
        from webapp.tts.streaming import iter_wav_pcm_chunks

        stored: list[bytes] = []
        sample_rate = 24000
        for rate, pcm in iter_wav_pcm_chunks(path, chunk_samples=chunk_samples):
            sample_rate = rate
            stored.append(pcm)
            yield rate, pcm
        if stored:
            from webapp.tts.streaming import write_pcm_chunks_to_wav
            write_pcm_chunks_to_wav(stored, path, sample_rate)

    def _create_placeholder_wav(self, path: str) -> None:
        import math
        import struct
        import wave

        sample_rate = 24000
        duration_s = 0.75
        frequency_hz = 523.25
        amplitude = 0.3
        n_frames = int(sample_rate * duration_s)

        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_frames):
                t = i / sample_rate
                envelope = min(1.0, t / 0.03, (duration_s - t) / 0.08)
                sample = amplitude * envelope * math.sin(2.0 * math.pi * frequency_hz * t)
                frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
            wf.writeframes(bytes(frames))
