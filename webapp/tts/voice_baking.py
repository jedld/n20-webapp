"""
Bake stable NPC voice reference clips for timbre-locked TTS.

VoiceDesign re-samples timbre on every utterance (and folds delivery/emotion into
the same instruct string), so voices drift mid-conversation. Baking generates one
neutral calibration line per NPC, saves it under the campaign's
``assets/voice_samples/``, then synthesis uses Qwen3 Base clone mode.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_BAKE_SAMPLE_TEXT = (
    "A deep, gravelly, and rough voice for dialogue. Warm but authoritative, just as I always speak."
)


def bake_sample_text() -> str:
    return os.environ.get("N20_TTS_BAKE_SAMPLE_TEXT", DEFAULT_BAKE_SAMPLE_TEXT).strip()


def baked_ref_text_path(campaign_root: Optional[str], npc_uid: str) -> Optional[Path]:
    sample = voice_sample_path(campaign_root, npc_uid)
    if sample is None:
        return None
    return sample.with_suffix(".ref.txt")


def load_baked_ref_text(campaign_root: Optional[str], npc_uid: str) -> str:
    sidecar = baked_ref_text_path(campaign_root, npc_uid)
    if sidecar and sidecar.is_file():
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return bake_sample_text()


def save_baked_ref_text(campaign_root: str, npc_uid: str, ref_text: str) -> None:
    sidecar = baked_ref_text_path(campaign_root, npc_uid)
    if sidecar is None:
        return
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(ref_text.strip(), encoding="utf-8")

DEFAULT_MODEL_DESIGN = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"


def bake_voices_enabled() -> bool:
    return os.environ.get("N20_TTS_BAKE_VOICES", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def clone_model_id() -> str:
    explicit = os.environ.get("QWEN3_TTS_CLONE_MODEL", "").strip()
    if explicit:
        return explicit
    return "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


def voice_sample_relative_path(npc_uid: str) -> Path:
    safe_uid = (npc_uid or "npc_unknown").strip().replace("/", "_")
    return Path("assets") / "voice_samples" / f"{safe_uid}.wav"


def voice_sample_path(campaign_root: Optional[str], npc_uid: str) -> Optional[Path]:
    if not campaign_root:
        return None
    return Path(campaign_root) / voice_sample_relative_path(npc_uid)


def discover_baked_sample(
    campaign_root: Optional[str],
    npc_uid: str,
    *,
    npc_type: Optional[str] = None,
) -> Optional[str]:
    """Find a baked WAV for an entity uid, optionally falling back to ``type_<npc_type>``."""
    path = voice_sample_path(campaign_root, npc_uid)
    if path and path.is_file():
        return str(path)
    if npc_type:
        from natural20.tts.campaign_voice_profiles import _slug

        type_uid = f"type_{_slug(npc_type)}"
        type_path = voice_sample_path(campaign_root, type_uid)
        if type_path and type_path.is_file():
            return str(type_path)
    return None


def design_model_id() -> str:
    model = (os.environ.get("QWEN3_TTS_MODEL") or "").strip()
    if model and "voicedesign" in model.lower():
        return model
    explicit = (os.environ.get("QWEN3_TTS_DESIGN_MODEL") or "").strip()
    if explicit:
        return explicit
    return DEFAULT_MODEL_DESIGN


def should_bake_voice_profile(profile) -> bool:
    """Whether this NPC profile should get a baked reference clip."""
    if not bake_voices_enabled():
        return False
    from webapp.tts.voice_profile import VoiceStrategy

    strategy = getattr(profile, "strategy", VoiceStrategy.AUTO)
    if strategy in (VoiceStrategy.CLONE, VoiceStrategy.PRESET):
        return False

    prompt = (getattr(profile, "prompt", None) or "").strip()
    locked = bool(getattr(profile, "locked", False))
    if not prompt and not locked:
        return False

    ref = getattr(profile, "reference_audio", None)
    if ref and Path(ref).is_file():
        return False
    campaign_root = getattr(profile, "campaign_root", None)
    npc_uid = getattr(profile, "npc_uid", None)
    if campaign_root and npc_uid and discover_baked_sample(str(campaign_root), str(npc_uid)):
        return False
    return True


def should_bake_qwen3_design_voice(
    *,
    resolved_strategy: str,
    reference_audio: Optional[str],
    campaign_root: Optional[str],
    npc_uid: str,
    profile_strategy: Optional[str] = None,
) -> bool:
    if not bake_voices_enabled():
        return False
    strategies = {resolved_strategy, profile_strategy or ""}
    strategies = {s.strip().lower() for s in strategies if s}
    if not strategies.intersection({"design", "voice_design", "auto"}):
        return False
    if reference_audio and Path(reference_audio).is_file():
        return False
    existing = discover_baked_sample(campaign_root, npc_uid)
    return existing is None
