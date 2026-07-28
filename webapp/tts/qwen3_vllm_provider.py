"""Qwen3-TTS via a remote vLLM-Omni sidecar (HTTP client only).

Set ``TTS_PROVIDER=qwen3_vllm`` and ``VLLM_OMNI_TTS_URL`` to the sidecar base URL.
See ``docs/TTS_VLLM_OMNI_SPIKE.md`` and ``services/vllm-omni-tts/``.
"""

from __future__ import annotations

import os
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from webapp.tts.base import TTSEngine, VoiceConfig

DEFAULT_BASE_URL = "http://127.0.0.1:8091"
DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_MAX_NEW_TOKENS = 2048
# Qwen3-TTS emits codec frames at 12.5 Hz (~80 ms/frame).
QWEN3_TTS_FRAME_RATE_HZ = 12.5


def _estimate_max_new_tokens(text: str, cap: int) -> int:
    """Budget enough codec frames for the spoken line (vLLM defaults to ~16 without this)."""
    chars = len(str(text or "").strip())
    # English dialogue is ~12–15 chars/s; add headroom for pauses and punctuation.
    estimated = max(96, int(chars * 1.5) + 48)
    return min(max(1, int(cap)), estimated)


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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _ref_text_for_audio(ref_audio: str) -> str:
    sidecar = Path(ref_audio).with_suffix(".ref.txt")
    if sidecar.is_file():
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return (
        "A steady voice for dialogue. Warm, clear, and natural, just as I always speak."
    )


@dataclass(frozen=True)
class VLLMOmniTTSConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float
    voice_prefix: str
    consent: str
    register_on_create: bool
    sample_rate: int
    stream_live: bool
    max_new_tokens: int

    @classmethod
    def from_env(cls) -> "VLLMOmniTTSConfig":
        port = os.environ.get("VLLM_PORT", "8091").strip() or "8091"
        default_url = f"http://127.0.0.1:{port}"
        sample_rate_raw = os.environ.get("VLLM_OMNI_TTS_SAMPLE_RATE", str(DEFAULT_SAMPLE_RATE))
        max_tokens_raw = os.environ.get(
            "VLLM_OMNI_TTS_MAX_NEW_TOKENS",
            str(DEFAULT_MAX_NEW_TOKENS),
        )
        try:
            sample_rate = int(sample_rate_raw)
        except (TypeError, ValueError):
            sample_rate = DEFAULT_SAMPLE_RATE
        try:
            max_new_tokens = int(max_tokens_raw)
        except (TypeError, ValueError):
            max_new_tokens = DEFAULT_MAX_NEW_TOKENS
        return cls(
            base_url=(os.environ.get("VLLM_OMNI_TTS_URL", default_url).strip() or default_url).rstrip("/"),
            model=os.environ.get("VLLM_OMNI_TTS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            api_key=os.environ.get("VLLM_OMNI_TTS_API_KEY", "none").strip() or "none",
            timeout=float(os.environ.get("VLLM_OMNI_TTS_TIMEOUT", "120") or "120"),
            voice_prefix=os.environ.get("VLLM_OMNI_TTS_VOICE_PREFIX", "n20_").strip() or "n20_",
            consent=(
                os.environ.get("VLLM_OMNI_TTS_CONSENT")
                or os.environ.get("VLLM_VOICE_CONSENT")
                or "campaign_baked"
            ).strip()
            or "campaign_baked",
            register_on_create=_env_bool("VLLM_OMNI_TTS_REGISTER_ON_CREATE", True),
            sample_rate=sample_rate,
            stream_live=_env_bool("VLLM_OMNI_TTS_STREAM", True),
            max_new_tokens=max(48, max_new_tokens),
        )


class VLLMOmniTTSClient:
    """Thin HTTP wrapper around vLLM-Omni speech endpoints."""

    def __init__(self, config: VLLMOmniTTSConfig, client: Any | None = None):
        self.config = config
        self._owns_client = client is None
        if client is None:
            import httpx

            headers = {}
            if config.api_key and config.api_key.lower() != "none":
                headers["Authorization"] = f"Bearer {config.api_key}"
            timeout = httpx.Timeout(
                connect=min(30.0, config.timeout),
                read=config.timeout,
                write=min(30.0, config.timeout),
                pool=min(30.0, config.timeout),
            )
            self._client = httpx.Client(
                base_url=config.base_url,
                headers=headers,
                timeout=timeout,
            )
        else:
            self._client = client

    def close(self) -> None:
        if self._owns_client and hasattr(self._client, "close"):
            self._client.close()

    def healthcheck(self) -> None:
        response = self._client.get("/v1/audio/voices")
        response.raise_for_status()

    def list_voice_names(self) -> set[str]:
        response = self._client.get("/v1/audio/voices")
        response.raise_for_status()
        return _parse_voice_names(response.json())

    def register_voice(self, name: str, ref_audio: str, ref_text: str) -> None:
        audio_path = Path(ref_audio)
        if not audio_path.is_file():
            raise FileNotFoundError(f"Reference audio not found: {ref_audio}")
        with audio_path.open("rb") as audio_file:
            response = self._client.post(
                "/v1/audio/voices",
                data={
                    "consent": self.config.consent,
                    "name": name,
                    "ref_text": ref_text,
                },
                files={"audio_sample": (audio_path.name, audio_file, "audio/wav")},
            )
        response.raise_for_status()

    def _speech_payload(self, text: str, voice: str, language: str, *, response_format: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": text,
            "voice": voice,
            "language": _resolve_language(language),
            "response_format": response_format,
            "task_type": "Base",
            "max_new_tokens": _estimate_max_new_tokens(text, self.config.max_new_tokens),
        }
        return payload

    def synthesize_wav(self, text: str, voice: str, language: str) -> bytes:
        payload = self._speech_payload(text, voice, language, response_format="wav")
        response = self._client.post("/v1/audio/speech", json=payload)
        response.raise_for_status()
        content = response.content
        if not content:
            raise RuntimeError("vLLM-Omni returned empty audio body")
        return content

    def synthesize_pcm_stream(self, text: str, voice: str, language: str) -> Iterator[bytes]:
        """Yield raw 16-bit PCM bytes as the sidecar decodes them."""
        payload = self._speech_payload(text, voice, language, response_format="pcm")
        payload["stream"] = True
        payload["stream_format"] = "audio"
        with self._client.stream("POST", "/v1/audio/speech", json=payload) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if chunk:
                    yield chunk


def _parse_voice_names(payload: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(payload, dict):
        items = payload.get("voices") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return names
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("id")
            if name:
                names.add(str(name))
        elif isinstance(item, str):
            names.add(item)
    return names


class Qwen3VLLMProvider(TTSEngine):
    """Remote Qwen3 Base clone via vLLM-Omni — no local GPU weights."""

    EMOTION_STYLES = {}

    def __init__(
        self,
        device: str = "cpu",
        cache_dir: str = "/tmp/natural20_tts",
        client: VLLMOmniTTSClient | None = None,
        config: VLLMOmniTTSConfig | None = None,
    ):
        del device  # sidecar owns GPU
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._config = config or VLLMOmniTTSConfig.from_env()
        self._client = client or VLLMOmniTTSClient(self._config)
        self._voice_registry: dict[str, VoiceConfig] = {}
        self._server_voices: set[str] = set()
        self._loaded = False
        self._backend_ready = False
        self._last_stream_output_path: Optional[str] = None

    def model_profile_key(self) -> str:
        return "qwen3_vllm"

    def initialize(self) -> None:
        if self._loaded:
            return
        try:
            self._client.healthcheck()
            self._server_voices = self._client.list_voice_names()
            self._backend_ready = True
            print(
                f"[TTS] Qwen3 vLLM-Omni connected ({self._config.base_url}, "
                f"model={self._config.model}, voices={len(self._server_voices)})"
            )
        except Exception as exc:
            self._backend_ready = False
            print(
                f"[TTS] Qwen3 vLLM-Omni unavailable at {self._config.base_url}: {exc}"
            )
        self._loaded = True

    def is_ready(self) -> bool:
        return bool(self._backend_ready)

    def _vllm_voice_name(self, npc_uid: str) -> str:
        prefix = self._config.voice_prefix
        if npc_uid.startswith(prefix):
            return npc_uid
        return f"{prefix}{npc_uid}"

    def _ensure_registered(
        self,
        vllm_voice: str,
        reference_audio: Optional[str],
        ref_text: str,
    ) -> None:
        if vllm_voice in self._server_voices:
            return
        if not self._config.register_on_create:
            return
        if not reference_audio or not os.path.exists(reference_audio):
            raise RuntimeError(
                f"Voice {vllm_voice!r} is not registered on vLLM-Omni and no "
                f"reference_audio is available to upload"
            )
        self._client.register_voice(vllm_voice, reference_audio, ref_text)
        self._server_voices.add(vllm_voice)
        print(f"[TTS] Registered vLLM voice {vllm_voice}")

    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        if not self._loaded:
            self.initialize()
        if not self._backend_ready:
            raise RuntimeError(
                f"vLLM-Omni TTS sidecar is not reachable at {self._config.base_url}"
            )

        profile = voice_metadata or {}
        campaign_root = str(profile.get("campaign_root") or "").strip() or None
        if not reference_audio and campaign_root:
            from webapp.tts.voice_baking import discover_baked_sample

            reference_audio = discover_baked_sample(
                campaign_root,
                npc_uid,
                npc_type=str(profile.get("npc_type") or "").strip() or None,
            )

        ref_text = str(profile.get("ref_text") or "").strip()
        if reference_audio and os.path.exists(reference_audio) and campaign_root:
            from webapp.tts.voice_baking import load_baked_ref_text

            ref_text = load_baked_ref_text(campaign_root, npc_uid) or ref_text
        if not ref_text and reference_audio:
            ref_text = _ref_text_for_audio(reference_audio)

        vllm_voice = self._vllm_voice_name(npc_uid)
        self._ensure_registered(vllm_voice, reference_audio, ref_text)

        se_path = reference_audio or str(self.cache_dir / f"vllm_ref_{npc_uid}.wav")
        extra = {
            "vllm_voice_name": vllm_voice,
            "language": _resolve_language(language or profile.get("language")),
            "voice_prompt": voice_prompt,
            "reference_audio": reference_audio,
            "ref_text": ref_text,
            "campaign_root": campaign_root,
            "resolved_strategy": "clone",
            "provider": "qwen3_vllm",
        }
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

    def _voice_request(self, npc_uid: str) -> tuple[str, str]:
        voice_config = self._voice_registry[npc_uid]
        meta = voice_config.extra_metadata or {}
        vllm_voice = str(meta.get("vllm_voice_name") or self._vllm_voice_name(npc_uid))
        language = str(meta.get("language") or "Auto")
        return vllm_voice, language

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        del emotion, instruct, volume
        if not self._backend_ready:
            raise RuntimeError("vLLM-Omni TTS sidecar is not ready")
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")

        if output_path is None:
            output_path = str(
                self.cache_dir / f"qwen3_vllm_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )

        vllm_voice, language = self._voice_request(npc_uid)

        audio_bytes = self._client.synthesize_wav(text, vllm_voice, language)
        Path(output_path).write_bytes(audio_bytes)
        self._last_stream_output_path = output_path
        return output_path

    def _generate_stream_buffered(
        self,
        text: str,
        npc_uid: str,
        *,
        output_path: Optional[str],
        chunk_samples: int,
    ) -> Iterator[tuple[int, bytes]]:
        from webapp.tts.streaming import iter_wav_pcm_chunks

        path = self.generate(text, npc_uid, output_path=output_path)
        self._last_stream_output_path = path
        yield from iter_wav_pcm_chunks(path, chunk_samples=chunk_samples)

    def _generate_stream_live(
        self,
        text: str,
        npc_uid: str,
        *,
        output_path: Optional[str],
        chunk_samples: int,
    ) -> Iterator[tuple[int, bytes]]:
        from webapp.tts.streaming import iter_rechunked_pcm, write_pcm_chunks_to_wav

        vllm_voice, language = self._voice_request(npc_uid)
        sample_rate = self._config.sample_rate
        stored_pcm: list[bytes] = []

        byte_iter = self._client.synthesize_pcm_stream(text, vllm_voice, language)
        for rate, pcm in iter_rechunked_pcm(byte_iter, sample_rate, chunk_samples=chunk_samples):
            stored_pcm.append(pcm)
            yield rate, pcm

        if output_path is None:
            output_path = str(
                self.cache_dir / f"qwen3_vllm_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )
        if stored_pcm:
            write_pcm_chunks_to_wav(stored_pcm, output_path, sample_rate)
            self._last_stream_output_path = output_path

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
        del emotion, instruct, volume
        if not self._backend_ready:
            raise RuntimeError("vLLM-Omni TTS sidecar is not ready")
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")

        if not self._config.stream_live:
            yield from self._generate_stream_buffered(
                text,
                npc_uid,
                output_path=output_path,
                chunk_samples=chunk_samples,
            )
            return

        try:
            yield from self._generate_stream_live(
                text,
                npc_uid,
                output_path=output_path,
                chunk_samples=chunk_samples,
            )
        except Exception as exc:
            print(f"[TTS] vLLM-Omni PCM stream failed ({exc}); falling back to buffered WAV")
            yield from self._generate_stream_buffered(
                text,
                npc_uid,
                output_path=output_path,
                chunk_samples=chunk_samples,
            )

    def last_stream_output_path(self) -> Optional[str]:
        return self._last_stream_output_path

    def persist_voice(self, npc_uid: str) -> dict:
        if npc_uid not in self._voice_registry:
            raise KeyError(f"No voice found for NPC '{npc_uid}'")
        return self._voice_registry[npc_uid].to_dict()

    def restore_voice(self, npc_uid: str, voice_data: dict) -> VoiceConfig:
        config = VoiceConfig.from_dict(voice_data)
        self._voice_registry[npc_uid] = config
        if self._backend_ready and self._config.register_on_create:
            meta = config.extra_metadata or {}
            vllm_voice = str(meta.get("vllm_voice_name") or self._vllm_voice_name(npc_uid))
            ref_audio = meta.get("reference_audio") or config.reference_audio
            ref_text = str(meta.get("ref_text") or "")
            if ref_audio and ref_text:
                try:
                    self._ensure_registered(vllm_voice, ref_audio, ref_text)
                except Exception as exc:
                    print(f"[TTS] vLLM voice restore skipped for {npc_uid}: {exc}")
        return config

    def list_locked_voices(self) -> list[str]:
        return list(self._voice_registry.keys())

    def remove_voice(self, npc_uid: str) -> bool:
        if npc_uid in self._voice_registry:
            del self._voice_registry[npc_uid]
            return True
        return False


class MockQwen3VLLMProvider(Qwen3VLLMProvider):
    """Mock remote provider for tests — no HTTP, placeholder WAV output."""

    def __init__(self, device: str = "cpu", cache_dir: str = "/tmp/natural20_tts"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._config = VLLMOmniTTSConfig.from_env()
        self._client = None  # type: ignore[assignment]
        self._voice_registry = {}
        self._server_voices = set()
        self._loaded = True
        self._backend_ready = True
        self._last_stream_output_path = None

    def initialize(self) -> None:
        self._loaded = True
        self._backend_ready = True

    def is_ready(self) -> bool:
        return True

    def _ensure_registered(self, vllm_voice: str, reference_audio: Optional[str], ref_text: str) -> None:
        del reference_audio, ref_text
        self._server_voices.add(vllm_voice)

    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        profile = voice_metadata or {}
        vllm_voice = self._vllm_voice_name(npc_uid)
        self._server_voices.add(vllm_voice)
        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=reference_audio or str(self.cache_dir / f"mock_vllm_{npc_uid}.wav"),
            style="calm",
            prompt_description=voice_prompt,
            reference_audio=reference_audio,
            extra_metadata={
                "vllm_voice_name": vllm_voice,
                "language": _resolve_language(language or profile.get("language")),
                "resolved_strategy": "clone",
                "provider": "mock_qwen3_vllm",
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
        del emotion, instruct, volume
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")
        if output_path is None:
            output_path = str(
                self.cache_dir / f"mock_qwen3_vllm_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )
        self._write_placeholder_wav(output_path)
        self._last_stream_output_path = output_path
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
        del emotion, instruct, volume, text
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")
        if output_path is None:
            output_path = str(
                self.cache_dir / f"mock_qwen3_vllm_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )

        from webapp.tts.streaming import iter_rechunked_pcm, write_pcm_chunks_to_wav

        sample_rate = DEFAULT_SAMPLE_RATE
        pcm_bytes = b"\x00\x01" * (sample_rate // 4) + b"\x00\x00" * (sample_rate // 4)

        def _chunks() -> Iterator[bytes]:
            half = len(pcm_bytes) // 2
            yield pcm_bytes[:half]
            yield pcm_bytes[half:]

        stored: list[bytes] = []
        for rate, pcm in iter_rechunked_pcm(_chunks(), sample_rate, chunk_samples=chunk_samples):
            stored.append(pcm)
            yield rate, pcm
        write_pcm_chunks_to_wav(stored, output_path, sample_rate)
        self._last_stream_output_path = output_path

    @staticmethod
    def _write_placeholder_wav(path: str, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        frames = sample_rate // 2
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * frames)
