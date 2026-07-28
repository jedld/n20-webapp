"""Shared toggle for autonomous NPC LLM calls (environment ticks, long rest)."""

from __future__ import annotations

import os
from typing import Any, Optional


_DISABLED_VALUES = frozenset({'0', 'false', 'no', 'off', 'disabled'})


def _env_enabled(name: str, *, default: str = '1') -> bool:
    return os.environ.get(name, default).strip().lower() not in _DISABLED_VALUES


def npc_background_llm_enabled(game_session: Any = None) -> bool:
    """Whether background NPC loops may invoke the LLM.

    Controlled by ``N20_NPC_BACKGROUND_LLM`` (default enabled) and optional
    ``game.yml`` block ``npc_background_llm.enabled``.
    """
    enabled = _env_enabled('N20_NPC_BACKGROUND_LLM', default='1')
    if game_session is None:
        return enabled

    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get('npc_background_llm') or {}
    if isinstance(block, dict) and 'enabled' in block:
        return bool(block.get('enabled'))
    return enabled


def resolve_background_llm_enabled(
    game_session: Any,
    *,
    config_block: Optional[dict] = None,
) -> bool:
    """Resolve per-feature ``llm_enabled`` with game.yml block override."""
    if isinstance(config_block, dict) and 'llm_enabled' in config_block:
        return bool(config_block.get('llm_enabled'))
    return npc_background_llm_enabled(game_session)
