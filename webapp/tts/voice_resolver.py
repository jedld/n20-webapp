"""
Resolve VoiceProfile + provider capabilities into a concrete VoicePlan.

This is the single place that decides whether an NPC uses clone, VoiceDesign,
preset speakers, or CosyVoice instruct — keeping providers thin adapters.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from webapp.tts.capabilities import ProviderCapabilities, get_provider_capabilities
from webapp.tts.voice_profile import VoicePlan, VoiceProfile, VoiceStrategy


def _engine_qwen3_mode(engine: object) -> Optional[str]:
    mode = getattr(engine, "_mode", None)
    if mode:
        return str(mode)
    model_id = str(getattr(engine, "model_id", "") or "").lower()
    if "voicedesign" in model_id:
        return "voice_design"
    if "base" in model_id:
        return "voice_clone"
    if "customvoice" in model_id:
        return "custom_voice"
    return None


def _reference_exists(path: Optional[str]) -> bool:
    if not path:
        return False
    return Path(path).is_file()


def resolve_voice_plan(
    profile: VoiceProfile,
    provider_name: str,
    *,
    capabilities: Optional[ProviderCapabilities] = None,
    engine: Optional[object] = None,
) -> VoicePlan:
    """
    Pick the best voice materialization strategy for this NPC and backend.

    Priority:
    1. Explicit ``voice.strategy`` in NPC YAML (when supported)
    2. Reference audio → clone (when file exists and backend supports it)
    3. Qwen3 VoiceDesign model + rich prompt → design
    4. Provider default (preset for Qwen3 CustomVoice, instruct for CosyVoice)
    """
    caps = capabilities or get_provider_capabilities(provider_name)
    design_prompt = profile.design_prompt()
    voice_prompt = (profile.prompt or design_prompt).strip() or design_prompt

    strategy = profile.strategy
    notes = ""

    if strategy == VoiceStrategy.AUTO:
        strategy = _auto_strategy(profile, provider_name, caps, engine)

    if strategy == VoiceStrategy.CLONE and not caps.supports_clone:
        strategy = caps.default_strategy
        notes = "clone requested but unsupported; using provider default"

    if strategy == VoiceStrategy.DESIGN and not caps.supports_design:
        if caps.supports_preset:
            strategy = VoiceStrategy.PRESET
            notes = "design requested but model lacks VoiceDesign; using preset+instruct"
        elif caps.supports_clone and _reference_exists(profile.reference_audio):
            strategy = VoiceStrategy.CLONE
        else:
            strategy = caps.default_strategy

    if strategy == VoiceStrategy.PRESET and not caps.supports_preset:
        strategy = caps.default_strategy

    if strategy == VoiceStrategy.INSTRUCT and VoiceStrategy.INSTRUCT not in caps.supported_strategies:
        strategy = caps.default_strategy

    # Qwen3: align strategy with loaded model variant when possible.
    qwen_mode = _engine_qwen3_mode(engine) if provider_name.startswith("qwen3") else None
    if _reference_exists(profile.reference_audio) and caps.supports_clone:
        strategy = VoiceStrategy.CLONE
        if qwen_mode == "voice_design" and not notes:
            notes = "using baked/reference audio for stable timbre"
    elif qwen_mode == "voice_design" and strategy in (
        VoiceStrategy.AUTO,
        VoiceStrategy.PRESET,
        VoiceStrategy.DESIGN,
    ):
        strategy = VoiceStrategy.DESIGN
    elif qwen_mode == "voice_clone" and _reference_exists(profile.reference_audio):
        strategy = VoiceStrategy.CLONE
    elif qwen_mode == "custom_voice" and strategy == VoiceStrategy.DESIGN:
        strategy = VoiceStrategy.PRESET
        if not notes:
            notes = "CustomVoice model: design traits folded into instruct on preset speaker"

    ref_audio = profile.reference_audio if strategy == VoiceStrategy.CLONE else None
    if strategy == VoiceStrategy.CLONE and not _reference_exists(ref_audio):
        if VoiceStrategy.INSTRUCT in caps.supported_strategies:
            strategy = VoiceStrategy.INSTRUCT
            ref_audio = None
            notes = (notes + "; " if notes else "") + "clone reference missing; using instruct"
        else:
            strategy = caps.default_strategy
            ref_audio = None

    if strategy == VoiceStrategy.DESIGN:
        voice_prompt = design_prompt

    return VoicePlan(
        strategy=strategy,
        voice_prompt=voice_prompt,
        design_prompt=design_prompt,
        reference_audio=ref_audio,
        provider=profile.provider or provider_name,
        notes=notes.strip("; "),
    )


def _auto_strategy(
    profile: VoiceProfile,
    provider_name: str,
    caps: ProviderCapabilities,
    engine: Optional[object],
) -> VoiceStrategy:
    env_default = (os.environ.get("N20_TTS_VOICE_STRATEGY") or "").strip().lower()
    if env_default:
        try:
            return VoiceStrategy(env_default)
        except ValueError:
            pass

    if _reference_exists(profile.reference_audio) and caps.supports_clone:
        return VoiceStrategy.CLONE

    if provider_name.startswith("qwen3"):
        qwen_mode = _engine_qwen3_mode(engine)
        if qwen_mode == "voice_design":
            return VoiceStrategy.DESIGN
        if qwen_mode == "voice_clone":
            return VoiceStrategy.CLONE
        prefer_design = os.environ.get("N20_TTS_PREFER_VOICE_DESIGN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if prefer_design and caps.supports_design:
            return VoiceStrategy.DESIGN
        return VoiceStrategy.PRESET

    if provider_name in ("cosyvoice", "mock_cosyvoice"):
        return VoiceStrategy.INSTRUCT

    if provider_name == "openvoice":
        return VoiceStrategy.CLONE if _reference_exists(profile.reference_audio) else caps.default_strategy

    return caps.default_strategy
