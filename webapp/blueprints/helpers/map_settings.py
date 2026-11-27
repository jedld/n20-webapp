"""Per-map display settings shared by the /map_settings endpoint and MCP tools.

Settings live at the top level of the map YAML file (like ``background_image``
and ``image_offset_px``) and are mirrored onto the live ``Map`` object's
``properties``. Extend ``MAP_SETTINGS_FIELDS`` to add new settings; the
endpoint, persistence, socket broadcast and MCP tool are generic.

Registry: ``key -> (default, kind, getter)`` where kind is ``'bool'`` or
``'int'`` and ``getter(map_obj) -> value`` reads the live value.
"""
from __future__ import annotations

import os
from typing import Any


MAP_SETTINGS_FIELDS: dict[str, tuple[Any, str, Any]] = {
    'background_stretch': (
        False,
        'bool',
        lambda map_obj: map_obj.background_stretch(),
    ),
}


def coerce_map_setting(key: str, raw: Any) -> Any:
    """Validate/normalize an incoming map setting value."""
    _default, kind, _getter = MAP_SETTINGS_FIELDS[key]
    if kind == 'bool':
        if isinstance(raw, str):
            return raw.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(raw)
    if kind == 'int':
        return int(raw)
    raise ValueError(f'Unknown setting kind for {key}')


def current_map_settings(map_obj) -> dict[str, Any]:
    """Read all known settings from a live Map object."""
    settings: dict[str, Any] = {}
    for key, (default, _kind, getter) in MAP_SETTINGS_FIELDS.items():
        try:
            settings[key] = getter(map_obj)
        except Exception:
            settings[key] = default
    return settings


def apply_map_settings(game_session, map_name: str, incoming: dict[str, Any]) -> dict[str, Any]:
    """Apply whitelisted settings to the live Map and persist them to its YAML.

    Returns the full current settings dict after the change.
    Raises KeyError for unknown keys / maps, ValueError for bad values.
    """
    map_obj = game_session.maps.get(map_name)
    if map_obj is None:
        raise KeyError(f'Unknown map: {map_name}')
    unknown = [k for k in incoming if k not in MAP_SETTINGS_FIELDS]
    if unknown:
        raise KeyError(f'Unknown map settings: {", ".join(unknown)}')

    applied: dict[str, Any] = {}
    for key, raw in incoming.items():
        value = coerce_map_setting(key, raw)
        if raw is None:
            map_obj.properties.pop(key, None)
            value = MAP_SETTINGS_FIELDS[key][0]
        else:
            map_obj.properties[key] = value
        applied[key] = value

    # Persist into the map YAML (same file resolution as background uploads).
    import yaml as _yaml

    maps_ref = game_session.game_properties.get('maps', {})
    rel_ref = maps_ref.get(map_name, f'maps/{map_name}')
    if not rel_ref.endswith('.yml'):
        rel_ref += '.yml'
    yml_path = os.path.join(game_session.root_path, rel_ref)
    if os.path.exists(yml_path):
        with open(yml_path, 'r') as f:
            content = _yaml.safe_load(f) or {}
    else:
        content = {}
    for key, value in applied.items():
        default = MAP_SETTINGS_FIELDS[key][0]
        if value == default:
            content.pop(key, None)
        else:
            content[key] = value
    with open(yml_path, 'w') as f:
        _yaml.safe_dump(content, f, sort_keys=False)

    return current_map_settings(map_obj)


def broadcast_map_settings(socketio, map_name: str, settings: dict[str, Any]) -> None:
    """Broadcast a ``map_settings`` socket message to all clients."""
    socketio.emit('message', {
        'type': 'map_settings',
        'message': {'map': map_name, 'settings': settings},
    })
