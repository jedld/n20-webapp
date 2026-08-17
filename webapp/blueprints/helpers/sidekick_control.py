"""Register / unregister player WebControllers for in-party sidekick NPCs."""

from __future__ import annotations

from typing import Iterable, Optional

from natural20.player_character import PlayerCharacter
from natural20.sidekick import (
    assign_owners,
    is_in_party_sidekick,
    iter_in_party_sidekicks,
    join_party,
    leave_party,
    sidekick_owners,
    sync_campaign_sidekicks,
)

from .pvp import ensure_controller_entry
from .runtime_state import get_current_game, get_game_session, get_output_logger, get_socketio


def _usernames(owners: Optional[Iterable[str]]) -> list[str]:
    result = []
    seen = set()
    for name in owners or []:
        normalized = str(name or '').strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def register_sidekick_control(entity, owners: Optional[Iterable[str]] = None,
                              current_game=None):
    """Ensure ``controllers`` + WebController users for an in-party sidekick."""
    if entity is None:
        return None
    game = current_game or get_current_game()
    session = getattr(game, 'game_session', None) if game is not None else get_game_session()
    if session is None or game is None:
        return None
    if isinstance(entity, PlayerCharacter):
        return None

    owner_list = _usernames(owners)
    if not owner_list:
        owner_list = sidekick_owners(session, entity)

    uid = str(getattr(entity, 'entity_uid', '') or '')
    if not uid:
        return None

    controllers = getattr(game, 'controllers', None)
    if controllers is None:
        entry = ensure_controller_entry(uid)
    else:
        entry = None
        for controller in controllers:
            if str(controller.get('entity_uid')) == uid:
                controller.setdefault('controllers', [])
                entry = controller
                break
        if entry is None:
            entry = {'entity_uid': uid, 'controllers': []}
            controllers.append(entry)

    existing = [str(name).strip().lower() for name in (entry.get('controllers') or [])]
    for name in owner_list:
        if name not in existing:
            existing.append(name)
    entry['controllers'] = existing

    registry = getattr(game, 'controller_registry', None)
    if registry is None:
        return entry
    controller = registry.ensure_web_controller_for_entity(entity)
    if controller is None:
        return entry
    for name in existing:
        try:
            controller.add_user(name)
        except Exception:
            pass
    return entry


def unregister_sidekick_control(entity):
    """Drop player owners from ``controllers`` so combat reverts to AI/LLM."""
    if entity is None:
        return
    current_game = get_current_game()
    uid = str(getattr(entity, 'entity_uid', '') or '')
    if not uid or current_game is None:
        return
    for controller in list(getattr(current_game, 'controllers', []) or []):
        if str(controller.get('entity_uid')) != uid:
            continue
        controller['controllers'] = []
        break
    web_controllers = getattr(current_game, 'web_controllers', None)
    if isinstance(web_controllers, dict):
        web_controllers.pop(entity, None)
        for key in list(web_controllers.keys()):
            if getattr(key, 'entity_uid', None) == uid:
                web_controllers.pop(key, None)


def _ui_updates_suppressed(current_game=None) -> bool:
    game = current_game if current_game is not None else get_current_game()
    try:
        return bool(game and game.realtime_ui_suppressed())
    except Exception:
        return False


def _entity_label(entity) -> str:
    try:
        if callable(getattr(entity, 'label', None)):
            return str(entity.label())
    except Exception:
        pass
    return str(getattr(entity, 'name', None) or getattr(entity, 'entity_uid', None) or 'Someone')


def notify_party_membership(entity, action: str, record=None, *, hostile: bool = False):
    """Refresh POV portraits and tell clients that party membership changed."""
    if entity is None or _ui_updates_suppressed():
        return
    name = _entity_label(entity)
    uid = str(getattr(entity, 'entity_uid', '') or '')
    owners = list((record or {}).get('owners') or [])
    if action == 'join':
        text = f'{name} joined the party.'
    elif action == 'leave':
        text = (
            f'{name} turned hostile and left the party.'
            if hostile
            else f'{name} left the party.'
        )
    else:
        text = f'{name} is now a party companion.'

    game = get_current_game()
    if game is not None:
        try:
            game.bump_render_epoch()
        except Exception:
            pass

    payload = {
        'type': 'party_membership',
        'message': {
            'action': action,
            'entity_uid': uid,
            'name': name,
            'owners': owners,
            'text': text,
            'hostile': bool(hostile),
        },
    }
    try:
        socketio = get_socketio()
    except Exception:
        socketio = getattr(game, 'socketio', None) if game is not None else None
    if socketio is not None:
        try:
            socketio.emit('message', {'type': 'refresh_map'})
            socketio.emit('message', payload)
        except Exception:
            pass
    try:
        logger = get_output_logger()
    except Exception:
        logger = None
    if logger is not None:
        try:
            logger.log(text, visibility='public')
        except Exception:
            pass


def join_party_and_control(entity, owners: Optional[Iterable[str]] = None, *,
                           notify: bool = True, **kwargs):
    session = get_game_session()
    record = join_party(session, entity, owners, **kwargs)
    register_sidekick_control(entity, record.get('owners') or owners)
    if notify:
        notify_party_membership(entity, 'join', record)
    return record


def leave_party_and_control(entity, *, hostile: bool = False, notify: bool = True):
    session = get_game_session()
    record = leave_party(session, entity, hostile=hostile)
    unregister_sidekick_control(entity)
    current_game = get_current_game()
    if current_game is not None and hasattr(current_game, 'update_group'):
        try:
            current_game.update_group(entity, 'b' if hostile else 'c')
        except Exception:
            pass
    if notify:
        notify_party_membership(entity, 'leave', record, hostile=hostile)
    return record


def assign_owners_and_control(entity, owners: Iterable[str], *, notify: bool = True):
    session = get_game_session()
    record = assign_owners(session, entity, owners)
    register_sidekick_control(entity, record.get('owners') or owners)
    if notify:
        notify_party_membership(entity, 'assign_owner', record)
    return record


def restore_sidekick_controllers(current_game=None):
    """Re-apply WebController users after campaign load or save/load."""
    game = current_game or get_current_game()
    session = getattr(game, 'game_session', None) if game is not None else get_game_session()
    if session is None:
        return []
    applied = []
    try:
        sync_campaign_sidekicks(session)
    except Exception:
        pass
    for entity in iter_in_party_sidekicks(session):
        owners = sidekick_owners(session, entity)
        register_sidekick_control(entity, owners, current_game=game)
        applied.append(getattr(entity, 'entity_uid', None))
    return applied


def can_invite_entity(session, entity, speaker=None) -> bool:
    if entity is None or session is None:
        return False
    if isinstance(entity, PlayerCharacter):
        return False
    if not getattr(entity, 'is_npc', lambda: False)():
        return False
    if is_in_party_sidekick(session, entity):
        return False
    if str(getattr(entity, 'group', '') or '').lower() == 'b':
        return False
    if speaker is not None and getattr(speaker, 'dead', lambda: False)():
        return False
    return True
