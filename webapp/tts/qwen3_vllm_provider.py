"""Qwen3-TTS via a remote vLLM-Omni sidecar (HTTP client only).

Set ``TTS_PROVIDER=qwen3_vllm`` and ``VLLM_OMNI_TTS_URL`` to the sidecar base URL.
See ``docs/TTS_VLLM_OMNI_SPIKE.md`` and ``services/vllm-omni-tts/``.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


class VoiceCloneRefRequired(RuntimeError):
    """vLLM-Omni Base clone rejected a speaker name that has no ICL sample."""


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


_DEFAULT_REF_TEXT = (
    "A steady voice for dialogue. Warm, clear, and natural, just as I always speak."
)


def _ref_text_for_audio(ref_audio: str) -> str:
    # Guard against empty/path-root strings which trigger
    # "PosixPath('.') has an empty name" when calling with_suffix().
    if not ref_audio or not Path(ref_audio).name:
        return _DEFAULT_REF_TEXT
    from webapp.tts.voice_baking import load_ref_text_for_audio

    return load_ref_text_for_audio(ref_audio, default=_DEFAULT_REF_TEXT)


def _audio_mime(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    if suffix == ".ogg":
        return "audio/ogg"
    return "audio/wav"


def _audio_data_url(path: str) -> str:
    """Encode a local audio file as a data URL for remote vLLM-Omni clone requests."""
    import base64

    payload = Path(path).read_bytes()
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{_audio_mime(path)};base64,{encoded}"


def _response_needs_ref_audio(status_code: int, body: str) -> bool:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    if code != 400:
        return False
    text = (body or "").lower()
    return "ref_audio" in text and ("built-in speaker" in text or "voice cloning" in text)


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
                files={"audio_sample": (audio_path.name, audio_file, _audio_mime(str(audio_path)))},
            )
        response.raise_for_status()

    def _speech_payload(
        self,
        text: str,
        voice: str,
        language: str,
        *,
        response_format: str,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": text,
            "voice": voice,
            "language": _resolve_language(language),
            "response_format": response_format,
            "task_type": "Base",
            "max_new_tokens": _estimate_max_new_tokens(text, self.config.max_new_tokens),
        }
        if ref_audio:
            payload["ref_audio"] = _audio_data_url(ref_audio)
            if ref_text:
                payload["ref_text"] = ref_text
        return payload

    def synthesize_wav(
        self,
        text: str,
        voice: str,
        language: str,
        *,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
    ) -> bytes:
        payload = self._speech_payload(
            text,
            voice,
            language,
            response_format="wav",
            ref_audio=ref_audio,
            ref_text=ref_text,
        )
        response = self._client.post("/v1/audio/speech", json=payload)
        if _response_needs_ref_audio(response.status_code, response.text):
            raise VoiceCloneRefRequired(response.text)
        response.raise_for_status()
        content = response.content
        if not content:
            raise RuntimeError("vLLM-Omni returned empty audio body")
        return content

    def synthesize_pcm_stream(
        self,
        text: str,
        voice: str,
        language: str,
        *,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
    ) -> Iterator[bytes]:
        """Yield raw 16-bit PCM bytes as the sidecar decodes them."""
        payload = self._speech_payload(
            text,
            voice,
            language,
            response_format="pcm",
            ref_audio=ref_audio,
            ref_text=ref_text,
        )
        payload["stream"] = True
        payload["stream_format"] = "audio"
        with self._client.stream("POST", "/v1/audio/speech", json=payload) as response:
            if response.status_code == 400:
                body = response.read().decode("utf-8", errors="replace")
                if _response_needs_ref_audio(response.status_code, body):
                    raise VoiceCloneRefRequired(body)
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
        self._needs_ref_audio: set[str] = set()
        self._registered_signatures: dict[str, tuple] = {}
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

    def _discover_reference_audio(
        self,
        npc_uid: str,
        reference_audio: Optional[str],
        campaign_root: Optional[str],
        npc_type: Optional[str] = None,
    ) -> Optional[str]:
        """Search multiple locations for a reference audio file.

        Order: explicit path -> campaign voice_samples -> templates voice_samples.
        """
        logger.info(
            "[TTS] Discovering ref_audio for NPC '%s' (campaign_root=%s, npc_type=%s)",
            npc_uid, campaign_root, npc_type,
        )
        # 1. Explicit path provided
        if reference_audio and os.path.exists(reference_audio):
            logger.info("[TTS] Found explicit ref_audio for NPC '%s': %s", npc_uid, reference_audio)
            return reference_audio

        # 2. Campaign voice_samples/<npc_uid>.wav|.mp3
        if campaign_root:
            from webapp.tts.voice_baking import (
                discover_baked_sample,
                first_existing_voice_sample,
                voice_sample_paths_for_stem,
            )

            discovered = discover_baked_sample(
                campaign_root,
                npc_uid,
                npc_type=npc_type,
            )
            if discovered:
                return discovered

            # 2b. Campaign assets/voice_<uid>.wav|.mp3 (pre-voice_samples convention)
            assets = Path(campaign_root) / "assets"
            found = first_existing_voice_sample(
                *voice_sample_paths_for_stem(assets, f"voice_{npc_uid}"),
                *voice_sample_paths_for_stem(assets, npc_uid),
            )
            if found:
                return found

        # 3. Cross-campaign search: scan user_levels/*/assets/voice_samples and legacy names
        from webapp.tts.voice_baking import first_existing_voice_sample, voice_sample_paths_for_stem

        project_root = Path(__file__).resolve().parents[3]  # natural_20.py/
        campaign_dirs: list[Path] = []
        env_campaigns = os.environ.get("N20_CAMPAIGNS_DIR", "").strip()
        if env_campaigns:
            campaign_dirs.append(Path(env_campaigns))
        campaign_dirs.append(project_root / "user_levels")
        seen_roots: set[str] = set()
        for campaigns_dir in campaign_dirs:
            if not campaigns_dir.is_dir():
                continue
            resolved = str(campaigns_dir.resolve())
            if resolved in seen_roots:
                continue
            seen_roots.add(resolved)
            for campaign_sub in sorted(campaigns_dir.iterdir()):
                if not campaign_sub.is_dir():
                    continue
                found = first_existing_voice_sample(
                    *voice_sample_paths_for_stem(campaign_sub / "assets" / "voice_samples", npc_uid),
                    *voice_sample_paths_for_stem(campaign_sub / "assets", f"voice_{npc_uid}"),
                    *voice_sample_paths_for_stem(campaign_sub / "assets", npc_uid),
                )
                if found:
                    return found

        # 4. Try templates/voice_samples/<npc_uid>.wav|.mp3 (bundled defaults)
        for base in [
            project_root / "templates",
            project_root,
        ]:
            found = first_existing_voice_sample(
                *voice_sample_paths_for_stem(base / "voice_samples", npc_uid),
            )
            if found:
                return found

        logger.warning("[TTS] No reference audio found for NPC '%s'", npc_uid)
        return None

    def _voice_signature(self, audio_path: Optional[str], fingerprint: str) -> tuple:
        from webapp.tts.voice_baking import audio_file_signature

        sig = audio_file_signature(audio_path)
        if not sig:
            return (str(fingerprint or ""), 0, 0, "")
        return (
            str(fingerprint or ""),
            int(sig["mtime_ns"]),
            int(sig["size"]),
            str(sig["name"]),
        )

    def _registration_is_current(
        self,
        vllm_voice: str,
        signature: tuple,
        campaign_root: Optional[str],
        npc_uid: str,
    ) -> bool:
        if signature[1] == 0 and signature[2] == 0:
            return False
        if self._registered_signatures.get(vllm_voice) == signature:
            return True
        if not campaign_root or not npc_uid:
            return False
        from webapp.tts.voice_baking import load_voice_profile_state

        state = load_voice_profile_state(campaign_root, npc_uid)
        if not state:
            return False
        stored = (
            str(state.get("fingerprint") or ""),
            int(state.get("mtime_ns") or 0),
            int(state.get("size") or 0),
            str(state.get("audio") or ""),
        )
        if stored == signature:
            return True
        # Bootstrap may stamp file identity with an empty fingerprint.
        # Same clip + newly known YAML hash is still current.
        return (
            stored[1:] == signature[1:]
            and stored[1] != 0
            and (not stored[0] or stored[0] == signature[0])
        )

    def _stamp_registration(
        self,
        vllm_voice: str,
        signature: tuple,
        campaign_root: Optional[str],
        npc_uid: str,
        audio_path: Optional[str],
        fingerprint: str,
    ) -> None:
        self._registered_signatures[vllm_voice] = signature
        if not campaign_root or not npc_uid or not audio_path:
            return
        from webapp.tts.voice_baking import save_voice_profile_state

        source = "provided" if Path(audio_path).suffix.lower() == ".mp3" else "baked"
        save_voice_profile_state(
            campaign_root,
            npc_uid,
            fingerprint=fingerprint or "",
            audio_path=audio_path,
            source=source,
        )

    def _ensure_registered(
        self,
        vllm_voice: str,
        reference_audio: Optional[str],
        ref_text: str,
        npc_uid: str = "",
        campaign_root: Optional[str] = None,
        npc_type: Optional[str] = None,
        fingerprint: str = "",
        force: bool = False,
    ) -> bool:
        effective_ref = reference_audio
        if not effective_ref or not os.path.exists(effective_ref):
            effective_ref = self._discover_reference_audio(
                npc_uid, effective_ref, campaign_root, npc_type
            )
        signature = self._voice_signature(effective_ref, fingerprint)

        if (
            not force
            and vllm_voice in self._server_voices
            and vllm_voice not in self._needs_ref_audio
        ):
            if self._registration_is_current(vllm_voice, signature, campaign_root, npc_uid):
                self._stamp_registration(
                    vllm_voice,
                    signature,
                    campaign_root,
                    npc_uid,
                    effective_ref,
                    fingerprint,
                )
                return True
            if not effective_ref or not os.path.exists(effective_ref):
                return True

        if not self._config.register_on_create:
            return vllm_voice in self._server_voices

        if not effective_ref or not os.path.exists(effective_ref):
            # Do not pretend the sidecar has this voice. A later speech call
            # with only ``voice=n20_<uid>`` is treated as a built-in speaker
            # and returns 400 "requires 'ref_audio' for voice cloning".
            logger.warning(
                "[TTS] Voice %r is not registered on vLLM-Omni and no "
                "reference_audio is available to upload for NPC %s. "
                "TTS will fail for this NPC until a voice sample is baked "
                "or manually registered.",
                vllm_voice,
                npc_uid or "(unknown)",
            )
            return False

        # Resolve ref_text from the ORIGINAL audio file (before resampling)
        # so the .ref.txt sidecar is found next to the original clip.
        original_ref = self._discover_reference_audio(
            npc_uid, reference_audio, campaign_root, npc_type
        ) or effective_ref
        if original_ref:
            sidecar_ref = _ref_text_for_audio(original_ref)
            if not ref_text or sidecar_ref != _DEFAULT_REF_TEXT:
                ref_text = sidecar_ref
                logger.info(
                    "[TTS] _ensure_registered sidecar ref_text for NPC '%s' (audio=%s): %r",
                    npc_uid, original_ref, ref_text,
                )

        target_rate = self._config.sample_rate
        ref_for_upload = effective_ref
        try:
            from webapp.tts.resample import ensure_sample_rate

            resample_cache = self.cache_dir / "resampled"
            resample_cache.mkdir(exist_ok=True)
            ref_for_upload = ensure_sample_rate(
                effective_ref,
                target_rate,
                cache_dir=str(resample_cache),
            )
        except ImportError:
            logger.warning(
                "[TTS] scipy/numpy not available — skipping reference audio "
                "resampling (sample rate mismatch may cause audio corruption)."
            )
        except Exception as exc:
            logger.warning("[TTS] Failed to resample reference audio: %s", exc)
        if ref_for_upload != effective_ref:
            logger.info(
                "[TTS] Resampled reference audio %s → %s (%d Hz)",
                effective_ref,
                ref_for_upload,
                target_rate,
            )

        try:
            self._client.register_voice(vllm_voice, ref_for_upload, ref_text)
            self._server_voices.add(vllm_voice)
            self._stamp_registration(
                vllm_voice,
                signature,
                campaign_root,
                npc_uid,
                effective_ref,
                fingerprint,
            )
            print(f"[TTS] Registered vLLM voice {vllm_voice} (from {ref_for_upload})")
            return True
        except Exception as exc:
            logger.warning(
                "[TTS] Failed to register vLLM voice %r: %s. "
                "Will retry on next synthesis attempt.",
                vllm_voice,
                exc,
            )
            return False

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
        npc_type = str(profile.get("npc_type") or "").strip() or None

        # Use broader discovery for reference audio
        discovered_ref = self._discover_reference_audio(
            npc_uid, reference_audio, campaign_root, npc_type
        )
        effective_ref = discovered_ref or reference_audio

        # Resolve ref_text: .ref.txt sidecar > baked text > profile ref_text > default
        # The sidecar (.ref.txt) is the actual transcript of the voice WAV and MUST
        # take priority — using the NPC voice prompt as ref_text produces garbled TTS
        # because the phonemes don't match the audio.
        ref_text = ""
        _DEFAULT_REF_TEXT = (
            "A steady voice for dialogue. Warm, clear, and natural, just as I always speak."
        )
        if effective_ref and os.path.exists(effective_ref):
            ref_text = _ref_text_for_audio(effective_ref)
            logger.info(
                "[TTS] Sidecar ref_text for NPC '%s' (audio=%s): %r",
                npc_uid, effective_ref, ref_text,
            )
        if campaign_root and (not ref_text or ref_text == _DEFAULT_REF_TEXT):
            from webapp.tts.voice_baking import load_baked_ref_text

            baked = load_baked_ref_text(campaign_root, npc_uid)
            if baked:
                logger.info(
                    "[TTS] Using baked ref_text for NPC '%s': %r",
                    npc_uid, baked,
                )
                ref_text = baked
        if not ref_text:
            ref_text = str(profile.get("ref_text") or "").strip()

        vllm_voice = self._vllm_voice_name(npc_uid)
        from webapp.tts.voice_baking import (
            audio_file_signature,
            voice_profile_fingerprint,
        )

        fingerprint = voice_profile_fingerprint(
            {
                **profile,
                "voice_prompt": voice_prompt,
                "prompt": voice_prompt,
                "accent": accent or profile.get("accent"),
                "language": language or profile.get("language"),
                "npc_uid": npc_uid,
                "campaign_root": campaign_root,
            }
        )
        self._ensure_registered(
            vllm_voice,
            effective_ref,
            ref_text,
            npc_uid=npc_uid,
            campaign_root=campaign_root,
            npc_type=npc_type,
            fingerprint=fingerprint,
        )

        se_path = effective_ref or str(self.cache_dir / f"vllm_ref_{npc_uid}.wav")
        extra = {
            "vllm_voice_name": vllm_voice,
            "language": _resolve_language(language or profile.get("language")),
            "voice_prompt": voice_prompt,
            "reference_audio": effective_ref,
            "ref_text": ref_text,
            "campaign_root": campaign_root,
            "resolved_strategy": "clone",
            "provider": "qwen3_vllm",
            "profile_fingerprint": fingerprint,
            "sample_signature": audio_file_signature(effective_ref),
            "npc_type": npc_type,
        }
        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=se_path,
            style="calm",
            prompt_description=voice_prompt,
            reference_audio=effective_ref,
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

    def _clone_refs(self, npc_uid: str) -> tuple[Optional[str], str]:
        voice_config = self._voice_registry[npc_uid]
        meta = voice_config.extra_metadata or {}
        ref_audio = str(meta.get("reference_audio") or voice_config.reference_audio or "").strip() or None
        if not ref_audio or not os.path.exists(ref_audio):
            discovered = self._discover_reference_audio(
                npc_uid,
                ref_audio,
                str(meta.get("campaign_root") or "").strip() or None,
                str(meta.get("npc_type") or "").strip() or None,
            )
            if discovered:
                ref_audio = discovered
                meta["reference_audio"] = discovered
                voice_config.reference_audio = discovered
        ref_text = str(meta.get("ref_text") or "").strip()
        if ref_audio:
            sidecar = _ref_text_for_audio(ref_audio)
            if not ref_text or sidecar != _DEFAULT_REF_TEXT:
                ref_text = sidecar
        return ref_audio, ref_text

    def _prepare_clone_request(self, npc_uid: str) -> tuple[str, str, Optional[str], str]:
        vllm_voice, language = self._voice_request(npc_uid)
        ref_audio, ref_text = self._clone_refs(npc_uid)
        meta = self._voice_registry[npc_uid].extra_metadata or {}
        self._ensure_registered(
            vllm_voice,
            ref_audio,
            ref_text,
            npc_uid=npc_uid,
            campaign_root=str(meta.get("campaign_root") or "").strip() or None,
            npc_type=str(meta.get("npc_type") or "").strip() or None,
            fingerprint=str(meta.get("profile_fingerprint") or ""),
        )
        include_ref = (
            vllm_voice not in self._server_voices
            or vllm_voice in self._needs_ref_audio
        )
        if include_ref:
            if not ref_audio or not os.path.exists(ref_audio):
                logger.warning(
                    "[TTS] Refusing synthesis for NPC '%s' (voice '%s'): "
                    "not registered on vLLM-Omni server (no ref_audio). "
                    "Bake a voice sample or register the voice manually.",
                    npc_uid,
                    vllm_voice,
                )
                raise ValueError(
                    f"Voice '{vllm_voice}' is not registered on the vLLM-Omni server "
                    f"(NPC '{npc_uid}'). Synthesis will fail without a reference audio sample."
                )
            return vllm_voice, language, ref_audio, ref_text
        return vllm_voice, language, None, ref_text

    def _mark_needs_ref_audio(self, vllm_voice: str) -> None:
        self._needs_ref_audio.add(vllm_voice)
        self._server_voices.discard(vllm_voice)

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        # Base clone copies the reference clip's dialect. YAML accent is not
        # re-applied here — bake VoiceDesign with accent-leading instruct.
        del emotion, instruct, volume
        if not self._backend_ready:
            raise RuntimeError("vLLM-Omni TTS sidecar is not ready")
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")
        vllm_voice, language, ref_audio, ref_text = self._prepare_clone_request(npc_uid)
        logger.info(
            "[TTS] Synthesizing for NPC '%s' (voice='%s', lang='%s', chars=%d, inline_ref=%s)",
            npc_uid, vllm_voice, language, len(text), bool(ref_audio),
        )
        if output_path is None:
            output_path = str(
                self.cache_dir / f"qwen3_vllm_{npc_uid}_{uuid.uuid4().hex[:8]}.wav"
            )

        try:
            audio_bytes = self._client.synthesize_wav(
                text, vllm_voice, language, ref_audio=ref_audio, ref_text=ref_text,
            )
        except VoiceCloneRefRequired as exc:
            logger.warning("[TTS] vLLM clone needs ref_audio for %s: %s", vllm_voice, exc)
            self._mark_needs_ref_audio(vllm_voice)
            vllm_voice, language, ref_audio, ref_text = self._prepare_clone_request(npc_uid)
            audio_bytes = self._client.synthesize_wav(
                text, vllm_voice, language, ref_audio=ref_audio, ref_text=ref_text,
            )
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

        vllm_voice, language, ref_audio, ref_text = self._prepare_clone_request(npc_uid)
        sample_rate = self._config.sample_rate
        stored_pcm: list[bytes] = []

        byte_iter = self._client.synthesize_pcm_stream(
            text, vllm_voice, language, ref_audio=ref_audio, ref_text=ref_text,
        )
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
        # Base clone copies the reference clip's dialect; instruct is unused.
        del emotion, instruct, volume
        if not self._backend_ready:
            raise RuntimeError("vLLM-Omni TTS sidecar is not ready")
        if npc_uid not in self._voice_registry:
            raise ValueError(f"No voice registered for NPC '{npc_uid}'")
        vllm_voice, language, ref_audio, _ref_text = self._prepare_clone_request(npc_uid)
        logger.info(
            "[TTS] Stream synthesizing for NPC '%s' (voice='%s', lang='%s', chars=%d, live=%s, inline_ref=%s)",
            npc_uid, vllm_voice, language, len(text), self._config.stream_live, bool(ref_audio),
        )
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
        except VoiceCloneRefRequired as exc:
            logger.warning("[TTS] vLLM stream clone needs ref_audio (%s); retrying buffered", exc)
            self._mark_needs_ref_audio(vllm_voice)
            yield from self._generate_stream_buffered(
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
                    campaign_root_meta = str(meta.get("campaign_root") or "").strip() or None
                    self._ensure_registered(
                        vllm_voice,
                        ref_audio,
                        ref_text,
                        npc_uid=npc_uid,
                        campaign_root=campaign_root_meta,
                        fingerprint=str(meta.get("profile_fingerprint") or ""),
                    )
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
        self._needs_ref_audio = set()
        self._registered_signatures = {}
        self._loaded = True
        self._backend_ready = True
        self._last_stream_output_path = None

    def initialize(self) -> None:
        self._loaded = True
        self._backend_ready = True

    def is_ready(self) -> bool:
        return True

    def _ensure_registered(
        self,
        vllm_voice: str,
        reference_audio: Optional[str],
        ref_text: str,
        npc_uid: str = "",
        campaign_root: Optional[str] = None,
        npc_type: Optional[str] = None,
        fingerprint: str = "",
        force: bool = False,
    ) -> None:
        del reference_audio, ref_text, npc_uid, campaign_root, npc_type, fingerprint, force
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
