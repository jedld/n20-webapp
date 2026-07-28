"""Per-model TTS capability and chunking configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Optional

_MIN_MAX_CHARS = 80
_MAX_MAX_CHARS = 800


@dataclass(frozen=True)
class TTSModelConfig:
    """Synthesis limits and capabilities for one TTS backend profile."""

    profile: str
    max_chars: int = 220
    chunk_pause_ms: int = 100
    supports_streaming: bool = True
    description: str = ''

    def with_env_overrides(self) -> 'TTSModelConfig':
        """Apply global then profile-specific environment overrides."""
        max_chars_env = _read_int_env('N20_TTS_MAX_CHARS')
        max_chars = self.max_chars if max_chars_env is None else max_chars_env
        pause_env = _read_int_env('N20_TTS_CHUNK_PAUSE_MS')
        pause_ms = self.chunk_pause_ms if pause_env is None else pause_env

        profile_suffix = self.profile.upper().replace('-', '_')
        profile_max = _read_int_env(f'N20_TTS_MAX_CHARS_{profile_suffix}')
        if profile_max is not None:
            max_chars = profile_max
        profile_pause = _read_int_env(f'N20_TTS_CHUNK_PAUSE_MS_{profile_suffix}')
        if profile_pause is not None:
            pause_ms = profile_pause

        max_chars = max(_MIN_MAX_CHARS, min(int(max_chars), _MAX_MAX_CHARS))
        pause_ms = max(0, min(int(pause_ms), 2000))
        return replace(self, max_chars=max_chars, chunk_pause_ms=pause_ms)


# Defaults derived from provider training/inference limits (e.g. CosyVoice token_max_length ~200).
MODEL_PROFILES: dict[str, TTSModelConfig] = {
    'cosyvoice_v2': TTSModelConfig(
        profile='cosyvoice_v2',
        max_chars=220,
        chunk_pause_ms=100,
        supports_streaming=True,
        description='CosyVoice 2 zero-shot (token_max_length ~200)',
    ),
    'cosyvoice_v3': TTSModelConfig(
        profile='cosyvoice_v3',
        max_chars=220,
        chunk_pause_ms=100,
        supports_streaming=True,
        description='CosyVoice 3 zero-shot / instruct (token_max_length ~200)',
    ),
    'cosyvoice': TTSModelConfig(
        profile='cosyvoice',
        max_chars=220,
        chunk_pause_ms=100,
        supports_streaming=True,
        description='CosyVoice default profile',
    ),
    'cosyvoice_openvoice_fallback': TTSModelConfig(
        profile='cosyvoice_openvoice_fallback',
        max_chars=350,
        chunk_pause_ms=100,
        supports_streaming=False,
        description='CosyVoice runtime fallback to OpenVoice cloning',
    ),
    'openvoice': TTSModelConfig(
        profile='openvoice',
        max_chars=400,
        chunk_pause_ms=80,
        supports_streaming=False,
        description='OpenVoice speaker cloning',
    ),
    'openvoice_gtts': TTSModelConfig(
        profile='openvoice_gtts',
        max_chars=500,
        chunk_pause_ms=60,
        supports_streaming=False,
        description='OpenVoice gTTS fallback (longer API limit, no clone)',
    ),
    'mock_cosyvoice': TTSModelConfig(
        profile='mock_cosyvoice',
        max_chars=220,
        chunk_pause_ms=100,
        supports_streaming=True,
        description='Mock CosyVoice for tests/dev',
    ),
    'qwen3_custom_voice': TTSModelConfig(
        profile='qwen3_custom_voice',
        max_chars=280,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Qwen3-TTS CustomVoice (preset speakers + instruct)',
    ),
    'qwen3_voice_design': TTSModelConfig(
        profile='qwen3_voice_design',
        max_chars=380,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Qwen3-TTS VoiceDesign (text-described voices)',
    ),
    'qwen3_voice_clone': TTSModelConfig(
        profile='qwen3_voice_clone',
        max_chars=380,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Qwen3-TTS Base (3-second voice clone)',
    ),
    'mock_qwen3': TTSModelConfig(
        profile='mock_qwen3',
        max_chars=280,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Mock Qwen3 for tests/dev',
    ),
    'qwen3_vllm': TTSModelConfig(
        profile='qwen3_vllm',
        max_chars=380,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Qwen3 Base clone via vLLM-Omni HTTP sidecar',
    ),
    'mock_qwen3_vllm': TTSModelConfig(
        profile='mock_qwen3_vllm',
        max_chars=380,
        chunk_pause_ms=80,
        supports_streaming=True,
        description='Mock vLLM-Omni provider for tests/dev',
    ),
}


def _read_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == '':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_model_profile(engine: Any, manager_provider_name: str) -> str:
    """
    Resolve the active synthesis profile for chunking/streaming decisions.

    Providers may implement ``model_profile_key()``; otherwise we infer from
  manager provider name and engine runtime flags.
    """
    profile_fn = getattr(engine, 'model_profile_key', None)
    if callable(profile_fn):
        key = str(profile_fn()).strip()
        if key:
            return key

    provider = (manager_provider_name or '').strip().lower()
    if provider == 'mock_cosyvoice':
        return 'mock_cosyvoice'
    if provider == 'mock_qwen3':
        return 'mock_qwen3'
    if provider in ('qwen3_vllm', 'mock_qwen3_vllm'):
        return provider
    if provider == 'qwen3':
        profile_fn = getattr(engine, 'model_profile_key', None)
        if callable(profile_fn):
            key = str(profile_fn()).strip()
            if key:
                return key
        return 'qwen3_custom_voice'
    if provider == 'openvoice':
        if getattr(engine, '_fallback_mode', False):
            return 'openvoice_gtts'
        return 'openvoice'
    if provider == 'cosyvoice':
        if getattr(engine, '_fallback_mode', False):
            return 'cosyvoice_openvoice_fallback'
        version = str(getattr(engine, '_model_version', '') or '').strip().lower()
        if version in ('v2', 'v3'):
            return f'cosyvoice_{version}'
        return 'cosyvoice'
    return provider or 'cosyvoice'


def get_model_config(
    engine: Any,
    manager_provider_name: str,
) -> TTSModelConfig:
    profile = resolve_model_profile(engine, manager_provider_name)
    base = MODEL_PROFILES.get(profile)
    if base is None:
        base = MODEL_PROFILES['cosyvoice']
        base = replace(base, profile=profile, description=f'Unknown profile {profile!r}; using cosyvoice defaults')
    return base.with_env_overrides()


def list_model_profiles() -> list[dict[str, Any]]:
    return [
        {
            'profile': cfg.profile,
            'max_chars': cfg.max_chars,
            'chunk_pause_ms': cfg.chunk_pause_ms,
            'supports_streaming': cfg.supports_streaming,
            'description': cfg.description,
        }
        for cfg in MODEL_PROFILES.values()
    ]
