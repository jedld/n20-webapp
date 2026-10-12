"""Let a dialog NPC speak in character right after it kills a player character.

The engine records who killed whom and how (``natural20/utils/death_info.py``)
and attaches it to the ``died`` event. This module turns that into a system
note for the killer's conversation LLM and asks for one short line (a taunt,
gloat, or lament), or ``[NO_RESPONSE]`` when silence fits the character.

Config (``game.yml``)::

    npc_kill_reactions:
      enabled: true        # default true

Per NPC YAML: ``kill_reactions: false`` opts a dialog NPC out.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label
from natural20.utils.death_info import describe_death

logger = logging.getLogger(__name__)

# The same death can reach the listener more than once (map copies of a PC,
# re-emitted events); one line per killer/victim within this window.
REACTION_DEDUPE_SECONDS = 60.0
_recent_reactions: Dict[tuple, float] = {}


def _recently_reacted(killer, victim) -> bool:
    now = time.monotonic()
    for key, stamp in list(_recent_reactions.items()):
        if now - stamp > REACTION_DEDUPE_SECONDS:
            _recent_reactions.pop(key, None)
    key = (getattr(killer, 'entity_uid', None), getattr(victim, 'entity_uid', None))
    if key in _recent_reactions:
        return True
    _recent_reactions[key] = now
    return False


def load_kill_reaction_config(game_session) -> Dict[str, Any]:
    props = getattr(game_session, 'game_properties', None) or {}
    block = props.get('npc_kill_reactions')
    if isinstance(block, bool):
        return {'enabled': block}
    if not isinstance(block, dict):
        block = {}
    return {'enabled': bool(block.get('enabled', True))}


def _call(entity, name, default=None):
    fn = getattr(entity, name, None)
    if not callable(fn):
        return default
    try:
        return fn()
    except Exception:
        return default


def should_react(killer, victim) -> bool:
    if killer is None or victim is None or killer is victim:
        return False
    if not isinstance(victim, PlayerCharacter):
        return False
    if isinstance(killer, PlayerCharacter) or not _call(killer, 'npc', False):
        return False
    if not getattr(killer, 'dialog', False):
        return False
    props = getattr(killer, 'properties', None) or {}
    if props.get('kill_reactions') is False:
        return False
    if _call(killer, 'dead', False) or not _call(killer, 'conscious', True):
        return False
    return True


def _victim_description(victim) -> str:
    label = entity_label(victim)
    details = []
    race = _call(victim, 'race')
    if race:
        details.append(str(race).replace('_', ' ').title())
    try:
        classes = victim.class_and_level() or []
        for klass, level in classes:
            details.append(f"{str(klass).replace('_', ' ').title()} {level}")
    except Exception:
        pass
    return f"{label} ({', '.join(details)})" if details else label


def _hp_state(entity) -> str:
    try:
        ratio = entity.hp() / max(1, entity.max_hp())
    except Exception:
        return 'unknown'
    if ratio >= 0.9:
        return 'unhurt'
    if ratio >= 0.5:
        return 'lightly wounded'
    if ratio >= 0.25:
        return 'bloodied'
    return 'badly wounded'


def _battle_lines(killer, victim, battle) -> List[str]:
    if battle is None:
        return []
    lines = []
    try:
        lines.append(f"Combat round: {int(getattr(battle, 'round', 0) or 0) + 1}.")
    except Exception:
        pass
    try:
        foes = [e for e in battle.opponents_of(killer) if e is not victim]
    except Exception:
        foes = []
    standing = [entity_label(e) for e in foes if not _call(e, 'dead', False) and _call(e, 'conscious', True)]
    fallen = [
        f"{entity_label(e)} ({'dead' if _call(e, 'dead', False) else 'unconscious'})"
        for e in foes if _call(e, 'dead', False) or not _call(e, 'conscious', True)
    ]
    if standing:
        lines.append(f"Foes still standing against you: {', '.join(standing)}.")
    else:
        lines.append("No foes remain standing against you — the fight is effectively won.")
    if fallen:
        lines.append(f"Other fallen foes: {', '.join(fallen)}.")
    return lines


def kill_reaction_note(killer, victim, death_info: Dict[str, Any], battle=None) -> str:
    info = dict(death_info or {})
    info.setdefault('victim_name', entity_label(victim))
    lines = [
        f"[Combat event] You just killed {_victim_description(victim)}, a player character.",
        describe_death(info, killer_pov=True),
        f"Your own condition: {_hp_state(killer)}.",
    ]
    lines.extend(_battle_lines(killer, victim, battle))
    lines.append(
        "If it fits your character, say one short line aloud mid-battle — a taunt, gloat, "
        "cold remark, or lament for the fallen — addressed to the victim or the survivors. "
        "If your character would stay silent, reply with [NO_RESPONSE]. "
        "Keep it to one or two short sentences. Do not use action, movement, or hostility tags; "
        "combat continues normally."
    )
    return '\n'.join(lines)


def _resolve_killer(session, death_info: Dict[str, Any]):
    uid = (death_info or {}).get('killer_uid')
    if not uid or session is None:
        return None
    try:
        return session.entity_by_uid(uid)
    except Exception:
        return None


def handle_died_event(event, *, game, conversation_service) -> bool:
    """Schedule a kill reaction for the ``died`` event. Returns True when scheduled."""
    victim = event.get('source')
    death_info = event.get('death_info') or {}
    session = getattr(game, 'game_session', None)
    if not load_kill_reaction_config(session).get('enabled', True):
        return False
    if conversation_service is None or getattr(conversation_service, 'llm_conversation_handler', None) is None:
        return False
    killer = _resolve_killer(session, death_info)
    if not should_react(killer, victim):
        return False
    if _recently_reacted(killer, victim):
        return False
    battle = None
    try:
        battle = game.get_current_battle()
    except Exception:
        battle = None
    note = kill_reaction_note(killer, victim, death_info, battle)

    def _job() -> None:
        try:
            conversation_service.handle_npc_kill_reaction(killer, victim, note)
        except Exception:
            logger.warning('NPC kill reaction failed for %s', getattr(killer, 'entity_uid', killer), exc_info=True)

    socketio = getattr(game, 'socketio', None)
    if socketio is not None and hasattr(socketio, 'start_background_task'):
        socketio.start_background_task(_job)
    else:
        _job()
    return True


def attach_npc_kill_reactions(conversation_service) -> bool:
    from webapp.blueprints.helpers.runtime_state import get_current_game, get_event_manager

    def _on_died(event):
        try:
            handle_died_event(event, game=get_current_game(), conversation_service=conversation_service)
        except Exception:
            logger.debug('NPC kill reaction scheduling failed', exc_info=True)

    try:
        get_event_manager().register_event_listener('died', _on_died)
    except Exception:
        logger.exception('Failed to register NPC kill reaction listener')
        return False
    return True
