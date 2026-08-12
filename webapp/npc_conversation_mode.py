"""Per-NPC conversation mode: LLM auto-replies vs DM manual control."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from natural20.player_character import PlayerCharacter

VALID_MODES = frozenset({'llm', 'manual'})
_STATE_KEY = 'npc_conversation_modes'


def _modes_map(game_session) -> Dict[str, str]:
    state = getattr(game_session, 'session_state', None) or {}
    raw = state.get(_STATE_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return raw


def _persist_modes_map(game_session, modes: Dict[str, str]) -> None:
    if not hasattr(game_session, 'session_state') or game_session.session_state is None:
        game_session.session_state = {}
    game_session.session_state[_STATE_KEY] = dict(modes)


def normalize_conversation_mode(mode: Optional[str]) -> str:
    normalized = str(mode or 'llm').strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(VALID_MODES))}")
    return normalized


def get_npc_conversation_mode(game_session, entity_uid: str) -> str:
    uid = str(entity_uid or '').strip()
    if not uid:
        return 'llm'
    return _modes_map(game_session).get(uid, 'llm')


def set_npc_conversation_mode(game_session, entity_uid: str, mode: str) -> str:
    uid = str(entity_uid or '').strip()
    if not uid:
        raise ValueError('entity_uid is required')
    normalized = normalize_conversation_mode(mode)
    modes = _modes_map(game_session)
    if normalized == 'llm':
        modes.pop(uid, None)
    else:
        modes[uid] = normalized
    _persist_modes_map(game_session, modes)
    return normalized


def is_npc_manual_conversation(game_session, entity) -> bool:
    if entity is None:
        return False
    uid = getattr(entity, 'entity_uid', None)
    if not uid:
        return False
    return get_npc_conversation_mode(game_session, uid) == 'manual'


def _entity_label(entity) -> str:
    try:
        return entity.label()
    except Exception:
        return str(getattr(entity, 'entity_uid', entity))


def partner_candidates_for_npc(current_game, npc) -> List[Dict[str, str]]:
    """Player characters on the same map (for DM manual dialog partner selection)."""
    candidates: Dict[str, Dict[str, str]] = {}
    try:
        battle_map = current_game.get_map_for_entity(npc)
    except Exception:
        battle_map = None

    if battle_map is not None:
        try:
            entities = list(battle_map.entities or [])
        except Exception:
            entities = []
        for ent in entities:
            if isinstance(ent, PlayerCharacter):
                uid = getattr(ent, 'entity_uid', None)
                if uid:
                    candidates[uid] = {'id': uid, 'name': _entity_label(ent)}

    for entry in list(getattr(npc, 'conversation_buffer', None) or []):
        source = entry.get('source')
        if isinstance(source, PlayerCharacter):
            uid = getattr(source, 'entity_uid', None)
            if uid:
                candidates[uid] = {'id': uid, 'name': _entity_label(source)}
        directed = entry.get('directed_to')
        if directed is None:
            directed = entry.get('targets')
        directed_list = directed if isinstance(directed, list) else ([directed] if directed else [])
        for target in directed_list:
            if isinstance(target, PlayerCharacter):
                uid = getattr(target, 'entity_uid', None)
                if uid:
                    candidates[uid] = {'id': uid, 'name': _entity_label(target)}

    return sorted(candidates.values(), key=lambda row: (row.get('name') or '').lower())


def npc_conversation_mode_payload(current_game, game_session, entity) -> Dict[str, Any]:
    uid = getattr(entity, 'entity_uid', None)
    dialog = bool(getattr(entity, 'dialog', False))
    return {
        'conversation_mode': get_npc_conversation_mode(game_session, uid),
        'dialog': dialog,
        'partner_candidates': partner_candidates_for_npc(current_game, entity) if dialog else [],
    }
