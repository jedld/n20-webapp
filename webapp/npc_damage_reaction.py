"""Trigger NPC LLM reactions when hit by attacks or spells outside combat."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from natural20.utils.npc_hit_reaction import (
    is_damage_hit_item,
    out_of_combat_hit_reaction_note,
)

logger = logging.getLogger(__name__)


def _damage_hit_items(action) -> List[Dict[str, Any]]:
    result = getattr(action, 'result', None) or []
    return [item for item in result if is_damage_hit_item(item)]


def _should_react_to_hit(target, attacker) -> bool:
    if target is None:
        return False
    if not callable(getattr(target, 'npc', None)) or not target.npc():
        return False
    if callable(getattr(target, 'dead', None)) and target.dead():
        return False
    if callable(getattr(target, 'conscious', None)) and not target.conscious():
        return False
    if attacker is target:
        return False
    return True


def process_out_of_combat_damage_reactions(game, action, *, in_combat: bool = False) -> None:
    """After an out-of-combat action resolves, let struck NPCs react via the NPC LLM."""
    if in_combat or action is None:
        return

    hits = _damage_hit_items(action)
    if not hits:
        return

    try:
        from webapp.blueprints.helpers.runtime_state import get_conversation_service

        conversation_service = get_conversation_service()
    except Exception:
        conversation_service = None
    if conversation_service is None:
        return
    if conversation_service.llm_conversation_handler is None:
        return

    session = getattr(game, 'game_session', None)
    for item in hits:
        target = item.get('target')
        attacker = item.get('source')
        if not _should_react_to_hit(target, attacker):
            continue
        battle_map = None
        try:
            battle_map = game.get_map_for_entity(target)
        except Exception:
            battle_map = None
        try:
            conversation_service.handle_npc_out_of_combat_hit(
                target,
                attacker,
                item,
                battle_map=battle_map,
            )
        except Exception:
            logger.warning(
                'NPC out-of-combat hit reaction failed for %s',
                getattr(target, 'entity_uid', target),
                exc_info=True,
            )


def collect_damage_hits_for_tests(action_result: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in (action_result or []) if is_damage_hit_item(item)]
