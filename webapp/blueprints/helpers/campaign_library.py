"""Campaign-level spawner library operations.

Backing service for the combined Spawner panel's add/delete/unique controls
(``/dm/library`` blueprint route and the MCP ``dm.library`` tool). All
mutations happen at the *campaign* level:

- Creating an NPC type writes ``<campaign>/npcs/<slug>.yml`` (a campaign-root
  file shadows bundled/imported sheets with the same stem).
- Marking a type unique writes a campaign copy of the effective sheet with an
  authored ``entity_uid`` (see ``natural20.npc.authored_unique_entity_uid``),
  so the engine keeps one instance per map set. Un-marking removes the
  authored uid from the campaign copy.
- Deleting an NPC type removes only campaign-root files; bundled (templates /
  imports / expansion packs) types are refused. Deleting a campaign override
  reveals the bundled base again.
- Objects live in merged ``items/objects.yml`` catalogues: campaign entries
  deep-merge over templates. Deleting a campaign entry removes it; deleting a
  bundled entry writes a ``key: null`` tombstone that the loader strips
  (``natural20.yaml_loader._drop_tombstones``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml


class SpawnerLibraryError(Exception):
    """User-facing campaign library error with an HTTP status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def slugify(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(value or '').lower()).strip('_')


def campaign_npc_dir(session) -> Path:
    return Path(session.root_path).resolve() / 'npcs'


def campaign_objects_file(session) -> Path:
    return Path(session.root_path).resolve() / 'items' / 'objects.yml'


def canonical_npc_stem(session, npc_type: str) -> str:
    getter = getattr(session, '_canonical_npc_type', None)
    if callable(getter):
        try:
            return str(getter(str(npc_type)))
        except Exception:
            pass
    return slugify(npc_type)


def npc_effective_file(session, npc_type: str):
    """Return ``(stem, path_or_None, campaign_owned)`` for an NPC type."""
    stem = canonical_npc_stem(session, npc_type)
    campaign_dir = campaign_npc_dir(session)
    for folder in session._category_roots('npcs', include_templates=True):
        candidate = Path(folder) / f'{stem}.yml'
        if candidate.is_file():
            return stem, candidate, Path(folder).resolve() == campaign_dir
    return stem, None, False


def campaign_npc_stems(session) -> set[str]:
    folder = campaign_npc_dir(session)
    if not folder.is_dir():
        return set()
    return {p.stem for p in folder.glob('*.yml')}


def _load_effective_sheet(session, stem: str) -> dict:
    from natural20.yaml_loader import load_campaign_resource_path
    try:
        data = load_campaign_resource_path(session.root_path, f'npcs/{stem}.yml')
    except FileNotFoundError:
        raise SpawnerLibraryError(f'NPC type "{stem}" not found', 404)
    if not isinstance(data, dict):
        raise SpawnerLibraryError(f'NPC type "{stem}" is not a valid sheet', 500)
    return data


def _write_npc_sheet(session, stem: str, data: dict) -> Path:
    folder = campaign_npc_dir(session)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{stem}.yml'
    with open(path, 'w', encoding='utf-8') as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
    return path


def create_npc_type(session, *, name: str, base_type: Optional[str] = None,
                    unique: bool = False, fields: Optional[dict] = None) -> dict:
    """Create a new campaign-level NPC type (optionally based on an existing one)."""
    fields = dict(fields or {})
    stem = slugify(name)
    if not stem:
        raise SpawnerLibraryError('NPC name is required')

    target = campaign_npc_dir(session) / f'{stem}.yml'
    if target.exists():
        raise SpawnerLibraryError(f'NPC type "{name}" already exists in this campaign', 409)

    if base_type:
        base_stem = canonical_npc_stem(session, base_type)
        _stem, base_path, _owned = npc_effective_file(session, base_type)
        if base_path is None:
            raise SpawnerLibraryError(f'Base NPC type "{base_type}" not found', 404)
        data = _load_effective_sheet(session, base_stem)
        for key in ('inherit', 'inherits'):
            data.pop(key, None)
    else:
        initial = (stem[:1] or 'n')
        hp = _positive_int(fields.get('max_hp'), 10)
        data = {
            'kind': str(name).strip(),
            'description': str(fields.get('description', '')),
            'size': 'medium',
            'race': ['humanoid'],
            'alignment': 'unaligned',
            'default_ac': _positive_int(fields.get('default_ac'), 12),
            'max_hp': hp,
            'hp_die': f'1d8',
            'speed': _positive_int(fields.get('speed'), 30),
            'passive_perception': 10,
            'token': [initial],
            'color': 'gray',
            'ability': {'str': 10, 'dex': 10, 'con': 10, 'int': 10, 'wis': 10, 'cha': 10},
            'languages': ['common'],
            'cr': 0,
            'xp': 0,
            'proficiency_bonus': 2,
            'actions': [],
        }

    # A copy must not inherit the base's identity/unique flag unless asked.
    for key in ('entity_uid', 'unique', 'label'):
        data.pop(key, None)

    data['name'] = str(name).strip()
    if fields.get('description') is not None:
        data['description'] = str(fields['description'])
    if 'max_hp' in fields:
        data['max_hp'] = _positive_int(fields.get('max_hp'), int(data.get('max_hp') or 10))
    if 'default_ac' in fields:
        data['default_ac'] = _positive_int(fields.get('default_ac'), int(data.get('default_ac') or 12))
    if 'speed' in fields:
        data['speed'] = _positive_int(fields.get('speed'), int(data.get('speed') or 30))
    if unique:
        data['entity_uid'] = stem
        data['unique'] = True

    _write_npc_sheet(session, stem, data)
    return {'id': stem, 'name': data['name'], 'unique': bool(unique), 'campaign_owned': True}


def _positive_int(value, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, 1) if default > 0 else max(number, 0)


def set_npc_unique(session, npc_type: str, unique: bool = True) -> dict:
    """Mark/unwrap a NPC type as unique by patching its campaign-level sheet."""
    stem, path, owned = npc_effective_file(session, npc_type)
    if path is None:
        raise SpawnerLibraryError(f'NPC type "{npc_type}" not found', 404)

    # Always rewrite through the effective (inherit-resolved) sheet so the
    # campaign file is self-contained and shadows every lower-priority root.
    data = _load_effective_sheet(session, stem)

    if unique:
        existing = data.get('entity_uid')
        data['entity_uid'] = existing if existing and not _looks_like_uuid(existing) else stem
        data['unique'] = True
    else:
        data.pop('entity_uid', None)
        data.pop('unique', None)
    for key in ('inherit', 'inherits'):
        data.pop(key, None)

    _write_npc_sheet(session, stem, data)
    return {'id': stem, 'unique': bool(unique), 'campaign_owned': True}


def _looks_like_uuid(value: Any) -> bool:
    return bool(re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        str(value or ''), re.I))


def placed_npc_locations(session, stem: str, authored_uid: Optional[str]) -> list[str]:
    """Campaign maps that currently hold an instance of this NPC type."""
    found = []
    for name, map_obj in (getattr(session, 'maps', None) or {}).items():
        try:
            entities = list(map_obj.entities)
        except Exception:
            continue
        for ent in entities:
            ent_type = str(getattr(ent, 'npc_type', '') or '').lower()
            ent_uid = str(getattr(ent, 'entity_uid', '') or '')
            if ent_type == stem.lower() or (authored_uid and ent_uid == authored_uid):
                found.append(str(name))
                break
    return found


def delete_npc_type(session, npc_type: str) -> dict:
    stem, path, owned = npc_effective_file(session, npc_type)
    if path is None:
        raise SpawnerLibraryError(f'NPC type "{npc_type}" not found', 404)
    if not owned:
        raise SpawnerLibraryError(
            f'"{stem}" is a bundled/imported NPC type and cannot be deleted from the campaign. '
            'Create a campaign NPC with the same name to override it instead.', 400)

    authored_uid = None
    try:
        from natural20.npc import authored_unique_entity_uid
        authored_uid = authored_unique_entity_uid(_load_effective_sheet(session, stem))
    except Exception:
        pass

    locations = placed_npc_locations(session, stem, authored_uid)
    if locations:
        raise SpawnerLibraryError(
            f'"{stem}" is placed on map(s): {", ".join(sorted(set(locations)))}. '
            'Delete those tokens on the map first.', 400)

    path.unlink()
    _stem2, still_path, _owned = npc_effective_file(session, stem)
    return {'id': stem, 'deleted': True, 'revealed_base': still_path is not None}


def campaign_object_stems(session) -> set[str]:
    """Object ids the campaign file itself defines (non-tombstone entries)."""
    raw = _load_raw_campaign_objects(session)
    return {key for key, value in raw.items() if value is not None}


def _load_raw_campaign_objects(session) -> dict:
    path = campaign_objects_file(session)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        raise SpawnerLibraryError(f'Campaign items/objects.yml is unreadable: {exc}', 500)
    if not isinstance(data, dict):
        raise SpawnerLibraryError('Campaign items/objects.yml must be a mapping of object ids', 500)
    return data


def _write_raw_campaign_objects(session, data: dict) -> None:
    path = campaign_objects_file(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def create_object_type(session, *, name: str, fields: Optional[dict] = None) -> dict:
    fields = dict(fields or {})
    object_id = slugify(fields.get('id') or name)
    if not object_id:
        raise SpawnerLibraryError('Object name is required')

    raw = _load_raw_campaign_objects(session)
    if raw.get(object_id) is not None:
        raise SpawnerLibraryError(f'Object "{object_id}" already exists in this campaign', 409)

    try:
        merged = session.load_yaml_file('items', 'objects') or {}
    except Exception:
        merged = {}
    if isinstance(merged, dict) and object_id in merged and merged.get(object_id) is not None:
        raise SpawnerLibraryError(
            f'Object "{object_id}" is already defined by the campaign base or templates. '
            'Delete it first if you want to replace it.', 409)

    entry: dict[str, Any] = {
        'name': str(name).strip(),
        'description': str(fields.get('description', '')),
        'color': str(fields.get('color') or 'brown'),
        'token': [str(fields.get('token') or 'o')[:1] or 'o'],
        'default_ac': _positive_int(fields.get('default_ac'), 15),
        'max_hp': _positive_int(fields.get('max_hp'), 1),
        'hp_die': '1d8',
        'passable': bool(fields.get('passable', False)),
        'opaque': bool(fields.get('opaque', True)),
    }
    category = str(fields.get('category') or '').strip().lower()
    if category and category != 'all':
        entry['spawner_category'] = category
    if fields.get('interactable'):
        entry['interactable'] = True
    if fields.get('item_class'):
        entry['item_class'] = str(fields['item_class'])

    raw[object_id] = entry
    _write_raw_campaign_objects(session, raw)
    return {'id': object_id, 'name': entry['name'], 'campaign_defined': True}


def delete_object_type(session, object_id: str) -> dict:
    object_id = slugify(object_id)
    if not object_id:
        raise SpawnerLibraryError('Object id is required')

    raw = _load_raw_campaign_objects(session)
    if raw.get(object_id) is not None:
        del raw[object_id]
        _write_raw_campaign_objects(session, raw)
        return {'id': object_id, 'deleted': True, 'tombstone': False}

    try:
        merged = session.load_yaml_file('items', 'objects') or {}
    except Exception:
        merged = {}
    if isinstance(merged, dict) and merged.get(object_id) is not None:
        # Bundled/imported object: mark it removed for this campaign with a
        # null tombstone (stripped by the catalogue loader).
        raw[object_id] = None
        _write_raw_campaign_objects(session, raw)
        return {'id': object_id, 'deleted': True, 'tombstone': True}

    raise SpawnerLibraryError(f'Object "{object_id}" not found', 404)


def invalidate_spawner_caches(app_obj, session) -> None:
    """Drop the /available_npcs + /available_objects caches and the session
    object catalogue so library edits show up on the next spawner load."""
    try:
        app_obj._available_npcs_cache = None
        app_obj._available_objects_cache = None
    except Exception:
        pass
    try:
        session.objects = {}
        session._objects_catalog_loaded = False
    except Exception:
        pass
