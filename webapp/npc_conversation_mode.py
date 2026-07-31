"""Per-NPC conversation mode: LLM (default) or manual (DM-controlled)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label

STATE_KEY = 'npc_conversation_mode'
VALID_MODES = frozenset({'llm', 'manual'})


def _mode_map(session) -> Dict[str, str]:
    state = getattr(session, 'session_state', None) or {}
    raw = state.get(STATE_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(uid): mode
        for uid, mode in raw.items()
        if str(mode) in VALID_MODES
    }


def get_npc_conversation_mode(session, entity_uid: Optional[str]) -> str:
    if not entity_uid:
        return 'llm'
    return _mode_map(session).get(str(entity_uid), 'llm')


def is_npc_manual_conversation(session, entity) -> bool:
    if entity is None:
        return False
    uid = getattr(entity, 'entity_uid', None)
    return get_npc_conversation_mode(session, uid) == 'manual'


def set_npc_conversation_mode(session, entity_uid: str, mode: str) -> str:
    normalized = str(mode or 'llm').strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(VALID_MODES))}")
    uid = str(entity_uid or '').strip()
    if not uid:
        raise ValueError('entity_uid is required')

    state = getattr(session, 'session_state', None)
    if state is None:
        state = {}
        session.session_state = state
    modes = dict(state.get(STATE_KEY) or {})
    if normalized == 'llm':
        modes.pop(uid, None)
    else:
        modes[uid] = normalized
    if modes:
        state[STATE_KEY] = modes
    else:
        state.pop(STATE_KEY, None)
    session.session_state = state
    return normalized


def list_dialog_partner_candidates(current_game, npc) -> List[Dict[str, Any]]:
    """Player characters on the same map as ``npc`` (for DM JRPG dialog)."""
    if npc is None or current_game is None:
        return []
    try:
        battle_map = current_game.get_map_for_entity(npc)
    except Exception:
        battle_map = None
    if battle_map is None:
        return []

    candidates = []
    for ent in getattr(battle_map, 'entities', []) or []:
        if not isinstance(ent, PlayerCharacter):
            continue
        if ent.entity_uid == getattr(npc, 'entity_uid', None):
            continue
        candidates.append({
            'id': ent.entity_uid,
            'name': entity_label(ent),
        })
    candidates.sort(key=lambda row: (row.get('name') or '').lower())
    return candidates


def npc_conversation_mode_payload(current_game, session, npc) -> Dict[str, Any]:
    uid = getattr(npc, 'entity_uid', None)
    return {
        'entity_uid': uid,
        'conversation_mode': get_npc_conversation_mode(session, uid),
        'is_npc': bool(callable(getattr(npc, 'is_npc', None)) and npc.is_npc()),
        'partner_candidates': list_dialog_partner_candidates(current_game, npc),
    }
