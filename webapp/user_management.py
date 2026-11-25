"""Campaign user accounts and player-character assignment (DM / MCP)."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from .blueprints.helpers.character_selection import collect_selectable_entries
from .blueprints.helpers.pvp import (
    ensure_character_entity_loaded,
    selectable_character_entry,
)
from .blueprints.helpers.runtime_state import (
    get_controllers,
    get_current_game,
    get_game_session,
    get_index_data,
    get_level,
    get_logins,
)

logger = logging.getLogger(__name__)
_USERNAME_RE = re.compile(r'^[a-z][a-z0-9_\-]{0,31}$')
_VALID_ROLES = frozenset({'player', 'dm'})


class UserManagementError(ValueError):
    """Raised when a user-management operation is invalid."""


def _normalize_username(username: str) -> str:
    return str(username or '').strip().lower()


def _validate_username(username: str) -> str:
    normalized = _normalize_username(username)
    if not normalized or not _USERNAME_RE.match(normalized):
        raise UserManagementError(
            'Username must be 1-32 characters: lowercase letters, digits, underscore, hyphen.'
        )
    return normalized


def _validate_roles(roles: Optional[List[str]]) -> List[str]:
    if roles is None:
        return ['player']
    if not isinstance(roles, list) or not roles:
        raise UserManagementError('roles must be a non-empty list')
    normalized = []
    for role in roles:
        role_key = str(role or '').strip().lower()
        if role_key not in _VALID_ROLES:
            raise UserManagementError(f"Invalid role: {role!r} (use 'player' and/or 'dm')")
        if role_key not in normalized:
            normalized.append(role_key)
    return normalized


def _campaign_index_path() -> str:
    game_session = get_game_session()
    if game_session is not None and getattr(game_session, 'root_path', None):
        return os.path.join(game_session.root_path, 'index.json')
    return os.path.join(get_level(), 'index.json')


def persist_campaign_users() -> str:
    """Write ``logins`` and ``default_controllers`` to campaign ``index.json``."""
    path = _campaign_index_path()
    if not os.path.isfile(path):
        raise UserManagementError(f'Campaign index not found: {path}')

    with open(path, 'r', encoding='utf-8') as handle:
        index_data = json.load(handle)

    index_data['logins'] = copy.deepcopy(list(get_logins() or []))
    index_data['default_controllers'] = copy.deepcopy(list(get_controllers() or []))

    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(index_data, handle, indent=2)

    try:
        memory_index = get_index_data()
        memory_index['logins'] = get_logins()
        memory_index['default_controllers'] = get_controllers()
    except Exception:
        pass

    logger.info('Persisted campaign users to %s', path)
    return path


def _find_login(username: str) -> Optional[Dict[str, Any]]:
    needle = _normalize_username(username)
    for entry in get_logins() or []:
        if _normalize_username(entry.get('name')) == needle:
            return entry
    return None


def _dm_login_count() -> int:
    return sum(1 for entry in get_logins() or [] if 'dm' in (entry.get('role') or []))


def _connected_usernames() -> set[str]:
    current_game = get_current_game()
    sid_map = getattr(current_game, 'username_to_sid', None) or {}
    return {_normalize_username(name) for name in sid_map.keys() if name}


def _assigned_characters_for(username: str) -> List[str]:
    needle = _normalize_username(username)
    assigned = []
    for controller in get_controllers() or []:
        controllers = [_normalize_username(name) for name in controller.get('controllers') or []]
        if needle in controllers:
            assigned.append(str(controller.get('entity_uid')))
    return assigned


def _ensure_controller_entry(entity_uid: str) -> Dict[str, Any]:
    entity_uid = str(entity_uid)
    for controller in get_controllers() or []:
        if str(controller.get('entity_uid')) == entity_uid:
            controller.setdefault('controllers', [])
            return controller
    controller = {'entity_uid': entity_uid, 'controllers': []}
    get_controllers().append(controller)
    return controller


def list_users() -> Dict[str, Any]:
    connected = _connected_usernames()
    users = []
    for entry in get_logins() or []:
        username = _normalize_username(entry.get('name'))
        users.append({
            'username': username,
            'roles': list(entry.get('role') or ['player']),
            'connected': username in connected,
            'assigned_characters': _assigned_characters_for(username),
        })

    selectable = []
    taken = set()
    for controller in get_controllers() or []:
        if controller.get('controllers'):
            taken.add(str(controller.get('entity_uid')))

    for character in collect_selectable_entries(get_index_data(), get_game_session()):
        name = str(character.get('name') or '')
        selectable.append({
            'character_uid': name,
            'description': character.get('description'),
            'taken': name in taken,
            'controllers': next(
                (
                    list(controller.get('controllers') or [])
                    for controller in get_controllers() or []
                    if str(controller.get('entity_uid')) == name
                ),
                [],
            ),
        })

    return {
        'users': users,
        'selectable_characters': selectable,
        'assignments': copy.deepcopy(list(get_controllers() or [])),
    }


def create_user(username: str, password: str, roles: Optional[List[str]] = None) -> Dict[str, Any]:
    name = _validate_username(username)
    if not str(password or '').strip():
        raise UserManagementError('password is required')
    if _find_login(name) is not None:
        raise UserManagementError(f'User already exists: {name}')

    entry = {
        'name': name,
        'password': str(password),
        'role': _validate_roles(roles),
    }
    get_logins().append(entry)
    persist_campaign_users()
    return {'username': name, 'roles': entry['role']}


def update_user(
    username: str,
    *,
    password: Optional[str] = None,
    roles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    name = _validate_username(username)
    entry = _find_login(name)
    if entry is None:
        raise UserManagementError(f'User not found: {name}')

    if password is not None:
        if not str(password).strip():
            raise UserManagementError('password cannot be empty')
        entry['password'] = str(password)

    if roles is not None:
        new_roles = _validate_roles(roles)
        if 'dm' in (entry.get('role') or []) and 'dm' not in new_roles and _dm_login_count() <= 1:
            raise UserManagementError('Cannot remove the last DM account')
        entry['role'] = new_roles

    persist_campaign_users()
    return {'username': name, 'roles': list(entry.get('role') or [])}


def delete_user(username: str) -> Dict[str, Any]:
    name = _validate_username(username)
    entry = _find_login(name)
    if entry is None:
        raise UserManagementError(f'User not found: {name}')

    if 'dm' in (entry.get('role') or []) and _dm_login_count() <= 1:
        raise UserManagementError('Cannot delete the last DM account')

    get_logins()[:] = [
        item for item in get_logins()
        if _normalize_username(item.get('name')) != name
    ]

    for controller in get_controllers() or []:
        controllers = controller.get('controllers') or []
        controller['controllers'] = [
            user for user in controllers if _normalize_username(user) != name
        ]

    _sync_web_controllers()
    persist_campaign_users()
    return {'username': name, 'deleted': True}


def assign_character(
    username: str,
    character_uid: str,
    *,
    spawn: bool = True,
) -> Dict[str, Any]:
    name = _validate_username(username)
    if _find_login(name) is None:
        raise UserManagementError(f'User not found: {name}')

    character_uid = str(character_uid or '').strip()
    if not character_uid:
        raise UserManagementError('character_uid is required')
    if selectable_character_entry(character_uid) is None:
        raise UserManagementError(f'Unknown selectable character: {character_uid}')

    for controller in get_controllers() or []:
        if (
            str(controller.get('entity_uid')) == character_uid
            and controller.get('controllers')
            and name not in {_normalize_username(user) for user in controller['controllers']}
        ):
            raise UserManagementError(f'Character {character_uid} is already assigned to another player')

    controller_entry = _ensure_controller_entry(character_uid)
    if name not in {_normalize_username(user) for user in controller_entry.get('controllers') or []}:
        controller_entry['controllers'].append(name)

    current_game = get_current_game()
    entity = ensure_character_entity_loaded(character_uid)
    if entity is None:
        controller_entry['controllers'] = [
            user for user in controller_entry.get('controllers') or []
            if _normalize_username(user) != name
        ]
        raise UserManagementError(f'Failed to load character sheet for {character_uid}')

    current_game._setup_controllers()

    spawned = []
    if spawn:
        spawned = current_game.spawn_player_for_user(name) or []
        current_game.ensure_user_entities_spawned(name)

    entity = current_game.get_entity_by_uid(character_uid)
    if entity is not None and current_game.get_pov_entity_for_user(name) is None:
        current_game.set_pov_entity_for_user(name, entity)
        entity_map = current_game.get_map_for_entity(entity)
        if entity_map is not None:
            try:
                current_game.switch_map_for_user(name, entity_map.name)
            except Exception:
                logger.exception('Failed to switch map for %s after character assignment', name)

    persist_campaign_users()
    return {
        'username': name,
        'character_uid': character_uid,
        'spawned': [getattr(ent, 'entity_uid', None) for ent in spawned],
        'assigned_characters': _assigned_characters_for(name),
    }


def unassign_character(
    username: str,
    character_uid: Optional[str] = None,
) -> Dict[str, Any]:
    name = _validate_username(username)
    if _find_login(name) is None:
        raise UserManagementError(f'User not found: {name}')

    removed = []
    for controller in get_controllers() or []:
        uid = str(controller.get('entity_uid'))
        if character_uid is not None and uid != str(character_uid):
            continue
        controllers = controller.get('controllers') or []
        if name in {_normalize_username(user) for user in controllers}:
            controller['controllers'] = [
                user for user in controllers if _normalize_username(user) != name
            ]
            removed.append(uid)

    if character_uid is not None and not removed:
        raise UserManagementError(
            f'User {name} is not assigned to character {character_uid}'
        )

    _remove_user_from_web_controllers(name, character_uid=character_uid)
    persist_campaign_users()
    return {
        'username': name,
        'removed_characters': removed,
        'assigned_characters': _assigned_characters_for(name),
    }


def _sync_web_controllers() -> None:
    current_game = get_current_game()
    try:
        current_game._setup_controllers()
    except Exception:
        logger.exception('Failed to sync web controllers after user-management change')


def _remove_user_from_web_controllers(username: str, *, character_uid: Optional[str] = None) -> None:
    current_game = get_current_game()
    needle = _normalize_username(username)
    for entity, controller in list(getattr(current_game, 'web_controllers', {}).items()):
        if character_uid is not None and str(getattr(entity, 'entity_uid', '')) != str(character_uid):
            continue
        users = getattr(controller, 'users', None)
        if not users:
            continue
        discard = {user for user in users if _normalize_username(user) == needle}
        for user in discard:
            users.discard(user)


def user_admin(op: str, **kwargs) -> Dict[str, Any]:
    operation = str(op or '').strip().lower()
    if operation == 'list':
        return list_users()
    if operation == 'create':
        return create_user(
            kwargs.get('username') or kwargs.get('name') or '',
            kwargs.get('password') or '',
            roles=kwargs.get('roles'),
        )
    if operation == 'update':
        return update_user(
            kwargs.get('username') or kwargs.get('name') or '',
            password=kwargs.get('password'),
            roles=kwargs.get('roles'),
        )
    if operation == 'delete':
        return delete_user(kwargs.get('username') or kwargs.get('name') or '')
    if operation == 'assign_character':
        return assign_character(
            kwargs.get('username') or kwargs.get('name') or '',
            kwargs.get('character_uid') or kwargs.get('character') or '',
            spawn=bool(kwargs.get('spawn', True)),
        )
    if operation == 'unassign_character':
        return unassign_character(
            kwargs.get('username') or kwargs.get('name') or '',
            character_uid=kwargs.get('character_uid') or kwargs.get('character'),
        )
    raise UserManagementError(
        "op must be one of: list, create, update, delete, assign_character, unassign_character"
    )
