"""Per-provider TTS capability registry for voice strategy resolution."""

from __future__ import annotations

from dataclasses import dataclass

from webapp.tts.voice_profile import VoiceStrategy


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a TTS backend can do when materializing an NPC voice."""

    name: str
    supported_strategies: frozenset[VoiceStrategy]
    default_strategy: VoiceStrategy
    supports_accent: bool = True
    supports_design: bool = False
    supports_clone: bool = True
    supports_preset: bool = False
    description: str = ""


PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "cosyvoice": ProviderCapabilities(
        name="cosyvoice",
        supported_strategies=frozenset(
            {VoiceStrategy.CLONE, VoiceStrategy.INSTRUCT, VoiceStrategy.AUTO}
        ),
        default_strategy=VoiceStrategy.INSTRUCT,
        supports_accent=True,
        supports_design=False,
        supports_clone=True,
        supports_preset=False,
        description="CosyVoice 3 zero-shot clone + Instruct2 delivery control",
    ),
    "mock_cosyvoice": ProviderCapabilities(
        name="mock_cosyvoice",
        supported_strategies=frozenset(
            {VoiceStrategy.CLONE, VoiceStrategy.INSTRUCT, VoiceStrategy.AUTO}
        ),
        default_strategy=VoiceStrategy.INSTRUCT,
        supports_accent=True,
        supports_design=False,
        supports_clone=True,
        supports_preset=False,
        description="Mock CosyVoice for tests/dev",
    ),
    "qwen3": ProviderCapabilities(
        name="qwen3",
        supported_strategies=frozenset(
            {
                VoiceStrategy.CLONE,
                VoiceStrategy.DESIGN,
                VoiceStrategy.PRESET,
                VoiceStrategy.AUTO,
            }
        ),
        default_strategy=VoiceStrategy.PRESET,
        supports_accent=False,
        supports_design=True,
        supports_clone=True,
        supports_preset=True,
        description="Qwen3-TTS CustomVoice / VoiceDesign / Base clone",
    ),
    "mock_qwen3": ProviderCapabilities(
        name="mock_qwen3",
        supported_strategies=frozenset(
            {
                VoiceStrategy.CLONE,
                VoiceStrategy.DESIGN,
                VoiceStrategy.PRESET,
                VoiceStrategy.AUTO,
            }
        ),
        default_strategy=VoiceStrategy.PRESET,
        supports_accent=False,
        supports_design=True,
        supports_clone=True,
        supports_preset=True,
        description="Mock Qwen3 for tests/dev",
    ),
    "qwen3_vllm": ProviderCapabilities(
        name="qwen3_vllm",
        supported_strategies=frozenset({VoiceStrategy.CLONE, VoiceStrategy.AUTO}),
        default_strategy=VoiceStrategy.CLONE,
        supports_accent=False,
        supports_design=False,
        supports_clone=True,
        supports_preset=False,
        description="Qwen3 Base clone via remote vLLM-Omni sidecar",
    ),
    "mock_qwen3_vllm": ProviderCapabilities(
        name="mock_qwen3_vllm",
        supported_strategies=frozenset({VoiceStrategy.CLONE, VoiceStrategy.AUTO}),
        default_strategy=VoiceStrategy.CLONE,
        supports_accent=False,
        supports_design=False,
        supports_clone=True,
        supports_preset=False,
        description="Mock vLLM-Omni client for tests/dev",
    ),
    "openvoice": ProviderCapabilities(
        name="openvoice",
        supported_strategies=frozenset({VoiceStrategy.CLONE, VoiceStrategy.AUTO}),
        default_strategy=VoiceStrategy.CLONE,
        supports_accent=False,
        supports_design=False,
        supports_clone=True,
        supports_preset=False,
        description="OpenVoice speaker embedding clone (gTTS fallback without reference)",
    ),
}


def get_provider_capabilities(provider_name: str) -> ProviderCapabilities:
    key = (provider_name or "").strip().lower()
    caps = PROVIDER_CAPABILITIES.get(key)
    if caps is not None:
        return caps
    return ProviderCapabilities(
        name=key or "unknown",
        supported_strategies=frozenset({VoiceStrategy.AUTO}),
        default_strategy=VoiceStrategy.AUTO,
        supports_accent=False,
        supports_design=False,
        supports_clone=False,
        supports_preset=False,
        description=f"Unknown provider {key!r}",
    )
