"""
Canonical NPC voice profile for multi-backend TTS.

VoiceProfile is the provider-neutral description of how an NPC should sound.
Providers receive a resolved VoicePlan (strategy + enriched prompts) from
``voice_resolver.resolve_voice_plan``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class VoiceStrategy(str, Enum):
    """How a backend should materialize a locked NPC voice."""

    AUTO = "auto"
    CLONE = "clone"        # reference-audio timbre lock (CosyVoice, Qwen3 Base, OpenVoice)
    DESIGN = "design"      # text-described timbre (Qwen3 VoiceDesign)
    PRESET = "preset"      # built-in speaker pool (Qwen3 CustomVoice)
    INSTRUCT = "instruct"  # zero-shot clone + delivery instruct (CosyVoice)


@dataclass
class VoiceProfile:
    """Provider-neutral NPC voice identity loaded from YAML / entity properties."""

    npc_uid: str
    prompt: str
    gender: Optional[str] = None
    age_band: Optional[str] = None
    race: Optional[str] = None
    label: Optional[str] = None
    accent: Optional[str] = None
    language: Optional[str] = None
    style: str = "calm"
    reference_audio: Optional[str] = None
    strategy: VoiceStrategy = VoiceStrategy.AUTO
    provider: Optional[str] = None
    traits: list[str] = field(default_factory=list)
    locked: bool = False
    campaign_root: Optional[str] = None

    def to_metadata(self) -> dict[str, Any]:
        """Serialize for VoiceConfig.extra_metadata['profile']."""
        return {
            "gender": self.gender,
            "age_band": self.age_band,
            "race": self.race,
            "label": self.label,
            "language": self.language,
            "strategy": self.strategy.value,
            "traits": list(self.traits),
            "provider": self.provider,
        }

    @classmethod
    def from_metadata(
        cls,
        npc_uid: str,
        prompt: str,
        metadata: Optional[dict[str, Any]],
        **kwargs: Any,
    ) -> "VoiceProfile":
        meta = metadata or {}
        strategy_raw = str(meta.get("strategy") or kwargs.pop("strategy", "auto")).strip().lower()
        try:
            strategy = VoiceStrategy(strategy_raw)
        except ValueError:
            strategy = VoiceStrategy.AUTO
        traits = meta.get("traits") or kwargs.pop("traits", [])
        if not isinstance(traits, list):
            traits = [str(traits)]
        return cls(
            npc_uid=npc_uid,
            prompt=prompt,
            gender=meta.get("gender") or kwargs.get("gender"),
            age_band=meta.get("age_band") or kwargs.get("age_band"),
            race=meta.get("race") or kwargs.get("race"),
            label=meta.get("label") or kwargs.get("label"),
            accent=kwargs.get("accent") or meta.get("accent"),
            language=meta.get("language") or kwargs.get("language"),
            style=kwargs.get("style") or meta.get("style") or "calm",
            reference_audio=kwargs.get("reference_audio") or meta.get("reference_audio"),
            strategy=strategy,
            provider=meta.get("provider") or kwargs.get("provider"),
            traits=[str(t).strip() for t in traits if str(t).strip()],
            locked=bool(kwargs.get("locked", meta.get("locked", False))),
        )

    def design_prompt(self) -> str:
        """
        Rich voice description for VoiceDesign / instruct backends.

        Combines explicit prompt, inferred demographics, and YAML traits into one
        stable string used when locking an NPC timbre.
        """
        parts: list[str] = []
        prompt = (self.prompt or "").strip()
        if prompt:
            parts.append(prompt)

        demographic: list[str] = []
        if self.age_band == "elderly":
            demographic.append("elderly")
        elif self.age_band == "young":
            demographic.append("young")
        elif self.age_band == "mature":
            demographic.append("mature middle-aged")

        if self.gender == "male":
            demographic.append("male")
        elif self.gender == "female":
            demographic.append("female")

        if self.race and self.race.lower() not in prompt.lower():
            demographic.append(f"{self.race} fantasy character")

        if demographic:
            parts.append(", ".join(demographic))

        for trait in self.traits:
            cleaned = trait.strip()
            if cleaned and cleaned.lower() not in prompt.lower():
                parts.append(cleaned)

        if self.accent and str(self.accent).lower() not in ("", "none", "american"):
            parts.append(f"{self.accent} accent")

        label = (self.label or "").strip()
        if label and label.lower() not in prompt.lower():
            parts.append(f"speaking as {label}")

        seen: set[str] = set()
        ordered: list[str] = []
        for part in parts:
            key = part.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(part.strip())
        return ". ".join(ordered) if ordered else f"Fantasy NPC voice for {label or self.npc_uid}"


@dataclass(frozen=True)
class VoicePlan:
    """Resolved materialization plan for a specific TTS backend."""

    strategy: VoiceStrategy
    voice_prompt: str
    design_prompt: str
    reference_audio: Optional[str] = None
    provider: Optional[str] = None
    notes: str = ""
