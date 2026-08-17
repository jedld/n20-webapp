"""ElevenLabs Voice Design as a bake-only timbre source.

Not a realtime TTS engine. ``bake_voice_sample`` calls
``POST /v1/text-to-voice/design``, writes the chosen preview MP3 under
``assets/voice_samples/``, and leaves runtime synthesis on Qwen3 / vLLM.

Set ``N20_TTS_BAKE_PROVIDER=elevenlabs`` and ``ELEVENLABS_API_KEY``.
Do not set ``TTS_PROVIDER=elevenlabs``.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from webapp.tts.base import TTSEngine, VoiceConfig

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
# v3 is the accent/emotion model; API still defaults to multilingual v2 if omitted.
DEFAULT_DESIGN_MODEL = "eleven_ttv_v3"
DEFAULT_OUTPUT_FORMAT = "mp3_24000_48"
# Official default is 5. Product docs: raise this when accent accuracy matters.
DEFAULT_GUIDANCE_SCALE = 5.0
_MIN_PREVIEW_CHARS = 100
_MAX_PREVIEW_CHARS = 1000
_MIN_DESCRIPTION_CHARS = 20
_MAX_DESCRIPTION_CHARS = 1000
_PREVIEW_PAD = (
    " I speak these words clearly, just as I always do in conversation, "
    "so every vowel and consonant of this voice can be heard."
)
_DESCRIPTION_PAD = " Fantasy NPC speaking clear English at a natural conversational pace."
_VAMPIRE_LORD_RE = re.compile(r"\bvampire\s+lord\b", re.I)
_VAMPIRE_RE = re.compile(r"\bvampires?\b", re.I)
_UNDEAD_RE = re.compile(r"\bundead\b", re.I)
_SPEAKING_AS_RE = re.compile(r"\.?\s*speaking as\s+.+$", re.I)
_EE_ACCENTS = frozenset(
    {
        "eastern european",
        "eastern_european",
        "romanian",
        "transylvanian",
        "slavic",
    }
)
_BAKE_ONLY_MESSAGE = (
    "ElevenLabs is a bake-only voice modeller. "
    "Keep TTS_PROVIDER on qwen3_vllm (or similar) and set "
    "N20_TTS_BAKE_PROVIDER=elevenlabs."
)


def elevenlabs_api_key() -> str:
    return (
        os.environ.get("ELEVENLABS_API_KEY", "").strip()
        or os.environ.get("ELEVEN_API_KEY", "").strip()
    )


def elevenlabs_preview_text(accent: Optional[str] = None) -> str:
    """Spoken line for Voice Design previews (API requires 100–1000 characters)."""
    from webapp.tts.voice_baking import bake_sample_text

    text = bake_sample_text(accent).strip()
    while len(text) < _MIN_PREVIEW_CHARS:
        text = f"{text}{_PREVIEW_PAD}".strip()
    return text[:_MAX_PREVIEW_CHARS]


def elevenlabs_voice_description(
    design_prompt: str,
    *,
    accent: Optional[str] = None,
) -> str:
    """``voice_description`` for POST /v1/text-to-voice/design (20–1000 chars).

    Official prompting: lead with language and regional dialect, then age/gender/timbre.
    ``thick`` is their recommended intensifier for a prominent accent.
    """
    from webapp.tts.accent_instruct import normalize_accent_label

    body = (design_prompt or "").strip()
    key = normalize_accent_label(accent)
    if body.lower().startswith("native english"):
        text = body
    elif key in _EE_ACCENTS:
        text = (
            "Native English. Thick Eastern European accent, Romanian or Transylvanian, "
            f"not American. Good quality. {body}"
        ).strip()
    elif key and key not in ("none", "american", "us", "usa", "english", "en"):
        label = (accent or key).strip()
        text = (
            f"Native English. Thick {label} accent, not American. "
            f"Good quality. {body}"
        ).strip()
    else:
        text = f"Native English. Good quality. {body}".strip()
    if len(text) < _MIN_DESCRIPTION_CHARS:
        text = f"{text}{_DESCRIPTION_PAD}".strip()
    return _sanitize_voice_description(text)[:_MAX_DESCRIPTION_CHARS]


def _sanitize_voice_description(text: str) -> str:
    """Rewrite terms ElevenLabs Voice Design commonly blocks for adult NPCs.

    Child-voice prompts are left unchanged; those generations stay blocked.
    """
    text = _SPEAKING_AS_RE.sub("", text).strip()
    text = _VAMPIRE_LORD_RE.sub("noble lord", text)
    text = _VAMPIRE_RE.sub("noble", text)
    text = _UNDEAD_RE.sub("pale", text)
    return text


def _guidance_scale() -> float:
    raw = os.environ.get("ELEVENLABS_GUIDANCE_SCALE", "").strip()
    if not raw:
        return DEFAULT_GUIDANCE_SCALE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_GUIDANCE_SCALE


def _should_enhance() -> bool:
    raw = os.environ.get("ELEVENLABS_SHOULD_ENHANCE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _pick_preview(previews: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if not previews:
        raise RuntimeError("ElevenLabs Voice Design returned no previews")
    if 0 <= index < len(previews):
        return previews[index]
    return max(previews, key=lambda item: float(item.get("duration_secs") or 0))


class ElevenLabsBakeProvider(TTSEngine):
    """HTTP client for ElevenLabs Voice Design bake. ``generate()`` is refused."""

    EMOTION_STYLES = {}

    def __init__(
        self,
        device: str = "cpu",
        cache_dir: str = "/tmp/natural20_tts",
        client: Any | None = None,
    ):
        del device
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._owns_client = client is None
        self._api_key = ""
        self._base_url = DEFAULT_BASE_URL
        self._loaded = False
        self._backend_ready = False
        self._voice_registry: dict[str, VoiceConfig] = {}

    def initialize(self) -> None:
        if self._loaded:
            return
        self._api_key = elevenlabs_api_key()
        self._base_url = (
            os.environ.get("ELEVENLABS_API_URL", "").strip() or DEFAULT_BASE_URL
        ).rstrip("/")
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "ELEVENLABS_API_KEY is required for ElevenLabs voice baking"
                )
            import httpx

            self._client = httpx.Client(
                base_url=self._base_url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                timeout=float(os.environ.get("ELEVENLABS_TIMEOUT", "180") or "180"),
            )
            self._owns_client = True
        self._backend_ready = True
        self._loaded = True
        print(
            f"[TTS] ElevenLabs bake provider ready ({self._base_url}, Voice Design only)"
        )

    def is_ready(self) -> bool:
        return bool(self._backend_ready)

    def close(self) -> None:
        if self._owns_client and self._client is not None and hasattr(self._client, "close"):
            self._client.close()

    def create_voice(
        self,
        npc_uid: str,
        voice_prompt: str,
        reference_audio: Optional[str] = None,
        accent: Optional[str] = None,
        language: Optional[str] = None,
        voice_metadata: Optional[dict] = None,
    ) -> VoiceConfig:
        del reference_audio, accent, language, voice_metadata
        config = VoiceConfig(
            npc_uid=npc_uid,
            se_path=str(self.cache_dir / f"elevenlabs_bake_{npc_uid}.mp3"),
            style="calm",
            prompt_description=voice_prompt,
            extra_metadata={"provider": "elevenlabs", "bake_only": True},
        )
        self._voice_registry[npc_uid] = config
        return config

    def bake_voice_sample(
        self,
        npc_uid: str,
        design_prompt: str,
        *,
        language: str = "en",
        campaign_root: str,
        accent: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> str:
        del language, gender
        from webapp.tts.voice_baking import save_baked_ref_text, voice_sample_path

        if not self._loaded:
            self.initialize()
        out = voice_sample_path(campaign_root, npc_uid, suffix=".mp3")
        if out is None:
            raise RuntimeError(f"Cannot bake voice sample without campaign_root for {npc_uid}")
        out.parent.mkdir(parents=True, exist_ok=True)
        preview_text = elevenlabs_preview_text(accent)
        print(
            f"[TTS] ElevenLabs Voice Design bake for {npc_uid}: "
            f"accent={accent or '(none)'} prompt={design_prompt[:180]}",
            flush=True,
        )
        audio_bytes, spoken_text = self._design_preview(
            design_prompt, preview_text, accent=accent
        )
        out.write_bytes(audio_bytes)
        save_baked_ref_text(campaign_root, npc_uid, spoken_text or preview_text)
        self.create_voice(npc_uid, design_prompt, accent=accent)
        print(f"[TTS] Baked ElevenLabs voice sample for {npc_uid} → {out}")
        return str(out)

    def _design_preview(
        self,
        design_prompt: str,
        preview_text: str,
        *,
        accent: Optional[str] = None,
    ) -> tuple[bytes, str]:
        if self._client is None:
            raise RuntimeError("ElevenLabs bake client is not initialized")
        model_id = (
            os.environ.get("ELEVENLABS_VOICE_DESIGN_MODEL", "").strip()
            or DEFAULT_DESIGN_MODEL
        )
        output_format = (
            os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "").strip()
            or DEFAULT_OUTPUT_FORMAT
        )
        try:
            preview_index = int(os.environ.get("N20_TTS_ELEVENLABS_PREVIEW_INDEX", "0") or "0")
        except ValueError:
            preview_index = 0
        payload: dict[str, Any] = {
            "voice_description": elevenlabs_voice_description(
                design_prompt, accent=accent
            ),
            "model_id": model_id,
            "text": preview_text,
            "auto_generate_text": False,
            "guidance_scale": _guidance_scale(),
            "should_enhance": _should_enhance(),
        }
        seed_raw = os.environ.get("ELEVENLABS_VOICE_SEED", "").strip()
        if seed_raw:
            try:
                payload["seed"] = int(seed_raw)
            except ValueError:
                pass
        response = self._client.post(
            "/v1/text-to-voice/design",
            params={"output_format": output_format},
            json=payload,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            body = getattr(response, "text", "") or ""
            raise RuntimeError(
                f"ElevenLabs Voice Design failed ({getattr(response, 'status_code', '?')}): "
                f"{body[:500]}"
            ) from exc
        data = response.json()
        previews = data.get("previews") if isinstance(data, dict) else None
        if not isinstance(previews, list):
            raise RuntimeError(f"Unexpected ElevenLabs Voice Design payload: {data!r}")
        preview = _pick_preview(previews, preview_index)
        encoded = str(preview.get("audio_base_64") or preview.get("audio_base64") or "")
        if not encoded:
            raise RuntimeError("ElevenLabs preview is missing audio_base_64")
        spoken = str(data.get("text") or preview_text).strip()
        return base64.b64decode(encoded), spoken

    def generate(
        self,
        text: str,
        npc_uid: str,
        emotion: Optional[str] = None,
        output_path: Optional[str] = None,
        instruct: Optional[str] = None,
        volume: Optional[str] = None,
    ) -> str:
        del text, npc_uid, emotion, output_path, instruct, volume
        raise RuntimeError(_BAKE_ONLY_MESSAGE)

    def persist_voice(self, npc_uid: str) -> dict:
        config = self._voice_registry.get(npc_uid)
        return config.to_dict() if config else {}

    def restore_voice(self, npc_uid: str, voice_data: dict) -> VoiceConfig:
        config = VoiceConfig.from_dict(voice_data)
        self._voice_registry[npc_uid] = config
        return config

    def list_locked_voices(self) -> list[str]:
        return list(self._voice_registry.keys())

    def remove_voice(self, npc_uid: str) -> bool:
        if npc_uid in self._voice_registry:
            del self._voice_registry[npc_uid]
            return True
        return False


class MockElevenLabsBakeProvider(ElevenLabsBakeProvider):
    """Test/dev baker: writes a placeholder MP3 without calling ElevenLabs."""

    def initialize(self) -> None:
        self._backend_ready = True
        self._loaded = True

    def _design_preview(
        self,
        design_prompt: str,
        preview_text: str,
        *,
        accent: Optional[str] = None,
    ) -> tuple[bytes, str]:
        del design_prompt, accent
        return b"ID3\x04\x00\x00n20-mock-elevenlabs", preview_text
