"""Shared toggle for autonomous NPC LLM calls (environment ticks, long rest).

Precedence: campaign ``.ai.yaml`` ``tasks.*`` -> ``game.yml`` block ->
webapp ``.ai.yaml`` -> environment variable (see ``webapp/ai_config.py``).
"""

from __future__ import annotations

import os
from typing import Any, Optional


_DISABLED_VALUES = frozenset({'0', 'false', 'no', 'off', 'disabled'})


def _env_enabled(name: str, *, default: str = '1') -> bool:
    return os.environ.get(name, default).strip().lower() not in _DISABLED_VALUES


def _game_yml_enabled(game_session: Any, block_name: str) -> Optional[bool]:
    if game_session is None:
        return None
    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get(block_name) or {}
    if isinstance(block, dict) and 'enabled' in block:
        return bool(block.get('enabled'))
    return None


def _resolve_toggle(dotted: str, game_session: Any, block_name: str, env_name: str) -> bool:
    from webapp.ai_config import get_ai_config

    config = get_ai_config()
    legacy = lambda: _game_yml_enabled(game_session, block_name)  # noqa: E731
    env = lambda: _env_enabled(env_name, default='1')  # noqa: E731
    if config is None:
        value = legacy()
        return env() if value is None else value
    value, _source = config.resolve(dotted, legacy=legacy, env=env, default=True)
    return bool(value)


def npc_background_llm_enabled(game_session: Any = None) -> bool:
    """Whether background NPC loops may invoke the LLM.

    ``tasks.background_llm`` in ``.ai.yaml``, ``game.yml`` ``npc_background_llm.enabled``,
    or ``N20_NPC_BACKGROUND_LLM`` (default enabled).
    """
    if not npc_autonomous_loops_enabled(game_session):
        return False
    return _resolve_toggle('tasks.background_llm', game_session, 'npc_background_llm', 'N20_NPC_BACKGROUND_LLM')


def npc_autonomous_loops_enabled(game_session: Any = None) -> bool:
    """Master switch for background NPC loops (environment ticks, long-rest sim).

    When disabled, autonomous loops are skipped even for DM ``force`` triggers.
    ``tasks.autonomous_loops`` in ``.ai.yaml`` wins; otherwise ``N20_NPC_LOOPS=0``
    is a hard kill switch, then ``game.yml`` ``npc_autonomous_loops.enabled``.
    """
    from webapp.ai_config import get_ai_config

    config = get_ai_config()
    if config is not None:
        value = config.get('tasks.autonomous_loops')
        if value is not None:
            return bool(value)
    if not _env_enabled('N20_NPC_LOOPS', default='1'):
        return False
    value = _game_yml_enabled(game_session, 'npc_autonomous_loops')
    return True if value is None else value


def resolve_background_llm_enabled(
    game_session: Any,
    *,
    config_block: Optional[dict] = None,
) -> bool:
    """Resolve per-feature ``llm_enabled`` with game.yml block override."""
    if isinstance(config_block, dict) and 'llm_enabled' in config_block:
        return bool(config_block.get('llm_enabled'))
    return npc_background_llm_enabled(game_session)
