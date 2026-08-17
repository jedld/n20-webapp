"""Login character-selection helpers (PC vs sidekick partition and campaign slots)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_sidekick_selectable(entry: Optional[Dict[str, Any]]) -> bool:
    """True when a selectable_characters-shaped dict is a campaign sidekick NPC."""
    if not isinstance(entry, dict):
        return False
    if str(entry.get('kind') or '').lower() == 'npc':
        return True
    if str(entry.get('role_label') or '').lower() == 'sidekick':
        return True
    sheet = str(entry.get('sheet') or '')
    return sheet.startswith('npcs/')


def partition_selectables(
    entries: Iterable[Dict[str, Any]] | None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split selection entries into player characters and sidekicks."""
    players: List[Dict[str, Any]] = []
    sidekicks: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if is_sidekick_selectable(entry):
            sidekicks.append(entry)
        else:
            players.append(entry)
    return players, sidekicks


def load_character_selection_settings(
    game_session=None,
    index_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Return ``player_slots`` and ``sidekick_slots`` from campaign config.

    ``game.yml`` ``character_selection`` overrides ``index.json``. Omit
    ``sidekick_slots`` (or set 0) for one hero pick from either section.
    ``require_sidekick: true`` is an alias for ``sidekick_slots: 1``.
    """
    if game_session is None or index_data is None:
        from .runtime_state import get_game_session, get_index_data

        if game_session is None:
            try:
                game_session = get_game_session()
            except Exception:
                game_session = None
        if index_data is None:
            try:
                index_data = get_index_data() or {}
            except Exception:
                index_data = {}

    merged: Dict[str, Any] = {}
    for source in (index_data or {}, getattr(game_session, 'game_properties', None) or {}):
        block = source.get('character_selection')
        if isinstance(block, dict):
            merged.update(block)

    player_slots = max(1, _as_int(merged.get('player_slots'), 1))
    if 'sidekick_slots' in merged:
        sidekick_slots = max(0, _as_int(merged.get('sidekick_slots'), 0))
    elif merged.get('require_sidekick'):
        sidekick_slots = 1
    else:
        sidekick_slots = 0
    return {
        'player_slots': player_slots,
        'sidekick_slots': sidekick_slots,
    }


def collect_selectable_entries(index_data: Optional[Dict[str, Any]] = None, game_session=None) -> List[Dict[str, Any]]:
    """PC rows from ``index.json`` plus campaign ``selectable: true`` sidekicks."""
    if index_data is None:
        from .runtime_state import get_index_data

        try:
            index_data = get_index_data() or {}
        except Exception:
            index_data = {}
    if game_session is None:
        from .runtime_state import get_game_session

        try:
            game_session = get_game_session()
        except Exception:
            game_session = None

    selectable = list(index_data.get('selectable_characters') or [])
    existing = {str(entry.get('name') or '').lower() for entry in selectable if isinstance(entry, dict)}
    if game_session is None:
        return selectable
    try:
        from natural20.sidekick import selectable_sidekick_entries

        for entry in selectable_sidekick_entries(game_session) or []:
            name = str(entry.get('name') or '').lower()
            if name and name not in existing:
                selectable.append(entry)
                existing.add(name)
    except Exception:
        pass
    return selectable


def taken_selectable_uids(game_session=None) -> Set[str]:
    """UIDs already claimed via controllers or in-party sidekick owners."""
    from .runtime_state import get_controllers

    taken: Set[str] = set()
    for controller in get_controllers() or []:
        if controller.get('controllers'):
            uid = str(controller.get('entity_uid') or '').strip()
            if uid:
                taken.add(uid)
    if game_session is None:
        from .runtime_state import get_game_session

        try:
            game_session = get_game_session()
        except Exception:
            game_session = None
    if game_session is None:
        return taken
    try:
        from natural20.sidekick import iter_in_party_sidekicks, sidekick_owners

        for entity in iter_in_party_sidekicks(game_session) or []:
            if not sidekick_owners(game_session, entity):
                continue
            uid = str(getattr(entity, 'entity_uid', '') or '').strip()
            if uid:
                taken.add(uid)
    except Exception:
        pass
    return taken


def _call(entity, name: str, default: Any = None) -> Any:
    attr = getattr(entity, name, default)
    if callable(attr):
        try:
            return attr()
        except TypeError:
            return default
    return attr if attr is not None else default


def _entity_name(entity) -> str:
    if callable(getattr(entity, 'label', None)):
        try:
            label = entity.label()
            if label:
                return str(label)
        except Exception:
            pass
    display = getattr(entity, 'display_name', None)
    if callable(display):
        try:
            display = display()
        except Exception:
            display = None
    if display:
        return str(display)
    name = _call(entity, 'name', '')
    return str(name or '')


def _classes_map(entity) -> Dict[str, Any]:
    raw = _call(entity, 'c_class', {})
    if isinstance(raw, dict) and raw:
        return raw
    props = getattr(entity, 'properties', None) or {}
    class_id = props.get('sidekick_class') or props.get('class')
    level = props.get('sidekick_level')
    if level is None:
        level = _call(entity, 'level', 1)
    if class_id:
        label = str(class_id).replace('sidekick_', '').replace('_', ' ')
        return {label: _as_int(level, 1)}
    kind = str(raw or props.get('kind') or 'npc').strip() or 'npc'
    return {kind: _as_int(level, 1)}


def _pretty_feature(name: Any) -> str:
    return str(name or '').replace('_', ' ').title()


def serialize_selection_character_details(entity) -> Dict[str, Any]:
    """Build the character-selection details payload for a PC or NPC sidekick."""
    from natural20.player_character import PlayerCharacter

    props = getattr(entity, 'properties', None) or {}
    is_pc = isinstance(entity, PlayerCharacter)
    name = _entity_name(entity)
    race = _call(entity, 'race', '') or ''
    if isinstance(race, (list, tuple)):
        race = ' '.join(str(part) for part in race if part)
    subrace = 'None'
    if callable(getattr(entity, 'subrace', None)):
        subrace = entity.subrace() or 'None'

    details: Dict[str, Any] = {
        'name': name,
        'display_name': name,
        'entity_uid': str(getattr(entity, 'entity_uid', '') or ''),
        'kind': 'pc' if is_pc else 'npc',
        'is_sidekick': not is_pc,
        'description': str(props.get('description') or ''),
        'sidekick_class': props.get('sidekick_class') or props.get('class'),
        'sidekick_role': props.get('sidekick_role') or props.get('role'),
        'race': race or 'Unknown',
        'subrace': subrace,
        'classes': _classes_map(entity),
        'level': _call(entity, 'level', 1),
        'hit_points': {
            'current': _call(entity, 'hp', 0),
            'maximum': _call(entity, 'max_hp', 0),
        },
        'armor_class': _call(entity, 'armor_class', 10),
        'speed': _call(entity, 'speed', 30),
        'ability_scores': {
            'str': _call(entity, 'ability_score_str', 10),
            'dex': _call(entity, 'ability_score_dex', 10),
            'con': _call(entity, 'ability_score_con', 10),
            'int': _call(entity, 'ability_score_int', 10),
            'wis': _call(entity, 'ability_score_wis', 10),
            'cha': _call(entity, 'ability_score_cha', 10),
        },
        'ability_modifiers': {
            'str': _call(entity, 'str_mod', 0),
            'dex': _call(entity, 'dex_mod', 0),
            'con': _call(entity, 'con_mod', 0),
            'int': _call(entity, 'int_mod', 0),
            'wis': _call(entity, 'wis_mod', 0),
            'cha': _call(entity, 'cha_mod', 0),
        },
        'proficiency_bonus': _call(entity, 'proficiency_bonus', 2),
        'passive_perception': _call(entity, 'passive_perception', 10),
        'languages': list(_call(entity, 'languages', []) or []),
        'equipment': {'weapons': [], 'armor': [], 'other': []},
        'spells': {'has_spells': False, 'spell_slots': {}, 'known_spells': []},
        'resources': {},
        'maneuvers': list(props.get('maneuvers') or []),
        'class_features': [],
        'racial_features': [],
        'journal': [],
    }

    try:
        equipped_items = entity.equipped_items() if callable(getattr(entity, 'equipped_items', None)) else []
    except Exception:
        equipped_items = []
    for item in equipped_items or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type', 'other')
        item_info = {
            'name': item.get('label', item.get('name', 'Unknown')),
            'damage': item.get('damage'),
            'range': item.get('range'),
            'properties': item.get('properties', []),
        }
        if item_type in ['melee_attack', 'ranged_attack']:
            details['equipment']['weapons'].append(item_info)
        elif item_type in ['armor', 'shield']:
            item_info['ac'] = item.get('ac')
            item_info['bonus_ac'] = item.get('bonus_ac')
            details['equipment']['armor'].append(item_info)
        else:
            details['equipment']['other'].append(item_info)

    has_spells = bool(_call(entity, 'has_spells', False)) if hasattr(entity, 'has_spells') else False
    details['spells']['has_spells'] = has_spells
    if has_spells:
        try:
            details['spells']['known_spells'] = entity.available_spells(None)
            classes = details['classes']
            if isinstance(classes, dict):
                for class_name in classes.keys():
                    class_slots = {}
                    for level in range(1, 10):
                        slots = entity.spell_slots_count(level, class_name)
                        if slots > 0:
                            class_slots[f'level_{level}'] = slots
                    if class_slots:
                        details['spells']['spell_slots'][class_name] = class_slots
        except Exception:
            pass

    important_features = [
        'action_surge', 'second_wind', 'sneak_attack', 'rage', 'bardic_inspiration',
        'channel_divinity', 'lay_on_hands', 'fighting_style', 'spellcasting',
        'cunning_action', 'uncanny_dodge', 'evasion', 'reliable_talent',
        'fancy_footwork', 'rakish_audacity', 'panache', 'sentinel',
        'martial_adept', 'arcane_recovery', 'abjuration_savant',
        'arcane_ward', 'projected_ward', 'improved_abjuration',
        'spell_resistance', 'extra_attack', 'helpful',
    ]
    if callable(getattr(entity, 'class_feature', None)):
        for feature in important_features:
            try:
                if entity.class_feature(feature):
                    details['class_features'].append(_pretty_feature(feature))
            except Exception:
                continue

    for name, pool in (getattr(entity, 'resources', None) or {}).items():
        details['resources'][name] = {
            'current': getattr(pool, 'current', 0),
            'max': getattr(pool, 'max_value', 0),
            'restore_on': getattr(pool, 'restore_on', ''),
        }

    race_props = getattr(entity, 'race_properties', None) or {}
    racial_features = race_props.get('race_features', []) if isinstance(race_props, dict) else []
    details['racial_features'] = [_pretty_feature(f) for f in (racial_features or [])[:5]]

    if is_pc:
        details['journal'] = list(getattr(entity, 'journal', None) or [])
    return details


def load_entity_for_selection_preview(character_name: str):
    """Resolve a selectable PC or sidekick for the details panel without claiming a spawn."""
    import os

    from .pvp import ensure_character_entity_loaded, selectable_character_entry
    from .runtime_state import get_current_game, get_game_session

    game = get_current_game()
    entity = game.get_entity_by_uid(character_name) if game is not None else None
    if entity is not None:
        return entity

    selectable = selectable_character_entry(character_name) or {}
    if is_sidekick_selectable(selectable):
        session = get_game_session()
        sheet = str(selectable.get('sheet') or '')
        npc_type = sheet[5:] if sheet.startswith('npcs/') else sheet
        npc_type = os.path.splitext(os.path.basename(npc_type))[0]
        if not npc_type:
            npc_type = str(character_name or '').removeprefix('npc_')
        overrides = dict(selectable.get('overrides') or {})
        overrides.setdefault('entity_uid', character_name)
        try:
            return session.npc(npc_type, {
                'name': selectable.get('display_name') or character_name,
                'overrides': overrides,
            })
        except Exception:
            return None
    return ensure_character_entity_loaded(character_name)

