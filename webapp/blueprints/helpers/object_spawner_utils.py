"""Helpers for DM object spawner listing and categorization."""

from __future__ import annotations

from typing import Any

SPAWNER_CATEGORIES: tuple[dict[str, str], ...] = (
    {"id": "all", "label": "All"},
    {"id": "terrain", "label": "Terrain"},
    {"id": "walls", "label": "Walls"},
    {"id": "doors", "label": "Doors"},
    {"id": "door_walls", "label": "Door Walls"},
    {"id": "traps", "label": "Traps"},
    {"id": "containers", "label": "Containers"},
    {"id": "interactive", "label": "Interactive"},
    {"id": "props", "label": "Props"},
)

_VALID_CATEGORY_IDS = {cat["id"] for cat in SPAWNER_CATEGORIES if cat["id"] != "all"}

_TERRAIN_OBJECT_IDS = frozenset({
    "water",
    "difficult_terrain",
    "tree",
    "briar",
    "chasm",
    "bottomless_pit",
    "ground",
})

_ITEM_CLASS_CATEGORY: dict[str, str] = {
    "StoneWall": "walls",
    "StoneWallDirectional": "walls",
    "DoorObject": "doors",
    "DoorObjectWall": "door_walls",
    "PitTrap": "traps",
    "ProximityTrigger": "traps",
    "Chest": "containers",
    "Switch": "interactive",
    "Teleporter": "interactive",
    "Chasm": "terrain",
}


def normalize_spawner_category(value: str) -> str:
    slug = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "doorwall": "door_walls",
        "door_wall": "door_walls",
        "doorwalls": "door_walls",
        "wall": "walls",
        "trap": "traps",
        "container": "containers",
        "prop": "props",
        "furniture": "props",
    }
    slug = aliases.get(slug, slug)
    return slug if slug in _VALID_CATEGORY_IDS else "props"


def resolve_spawner_category(object_id: str, object_data: dict[str, Any]) -> str:
    explicit = object_data.get("spawner_category")
    if explicit:
        return normalize_spawner_category(str(explicit))

    item_class = str(object_data.get("item_class") or "").strip()
    if item_class in _ITEM_CLASS_CATEGORY:
        return _ITEM_CLASS_CATEGORY[item_class]

    object_key = (object_id or "").strip().lower()
    if object_key in _TERRAIN_OBJECT_IDS:
        return "terrain"

    if object_data.get("swimmable"):
        return "terrain"

    movement_cost = object_data.get("movement_cost")
    if movement_cost is not None:
        try:
            if float(movement_cost) > 1 and item_class not in _ITEM_CLASS_CATEGORY:
                return "terrain"
        except (TypeError, ValueError):
            pass

    if object_data.get("allow_hide") or object_data.get("cover"):
        return "terrain"

    if object_data.get("interactable") and item_class not in _ITEM_CLASS_CATEGORY:
        return "interactive"

    return "props"


def build_available_object_entry(object_id: str, object_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(object_data, dict):
        return None
    if object_data.get("placeable") is False:
        return None

    if object_data.get("token_editor_image"):
        token_image = object_data["token_editor_image"]
    else:
        token_image = f"{object_id}.png"

    item_class = str(object_data.get("item_class") or "")
    category = resolve_spawner_category(object_id, object_data)

    return {
        "id": object_id,
        "name": object_data.get("name", object_id.replace("_", " ").title()),
        "description": object_data.get("description", ""),
        "image": token_image,
        "ac": object_data.get("default_ac", "N/A"),
        "hp": object_data.get("max_hp", "N/A"),
        "passable": object_data.get("passable", False),
        "opaque": object_data.get("opaque", True),
        "color": object_data.get("color", "brown"),
        "item_class": item_class,
        "category": category,
    }


def build_available_objects_list(all_objects: dict[str, Any]) -> list[dict[str, Any]]:
    object_list: list[dict[str, Any]] = []
    for object_id, object_data in (all_objects or {}).items():
        if not object_id:
            continue
        entry = build_available_object_entry(str(object_id), object_data)
        if entry:
            object_list.append(entry)
    object_list.sort(key=lambda item: item["name"].lower())
    return object_list
