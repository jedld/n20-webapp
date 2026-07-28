"""Tests for object spawner categorization helpers."""

from __future__ import annotations

from webapp.blueprints.helpers.object_spawner_utils import (
    build_available_object_entry,
    build_available_objects_list,
    resolve_spawner_category,
)


def test_resolve_spawner_category_for_walls_and_doors():
    assert resolve_spawner_category("stone_wall", {"item_class": "StoneWall"}) == "walls"
    assert resolve_spawner_category("stone_wall_t", {"item_class": "StoneWallDirectional"}) == "walls"
    assert resolve_spawner_category("wooden_door", {"item_class": "DoorObject"}) == "doors"
    assert resolve_spawner_category("corner_door_tl", {"item_class": "DoorObjectWall"}) == "door_walls"


def test_resolve_spawner_category_for_terrain_objects():
    assert resolve_spawner_category("water", {"passable": True}) == "terrain"
    assert resolve_spawner_category("briar", {"movement_cost": 2, "passable": True}) == "terrain"


def test_resolve_spawner_category_honors_explicit_yaml_override():
    assert resolve_spawner_category(
        "custom_thing",
        {"item_class": "DoorObject", "spawner_category": "props"},
    ) == "props"


def test_build_available_objects_list_skips_non_placeable():
    objects = {
        "barrel": {"name": "Barrel", "placeable": True},
        "secret": {"name": "Secret", "placeable": False},
    }
    entries = build_available_objects_list(objects)
    assert [entry["id"] for entry in entries] == ["barrel"]


def test_build_available_object_entry_includes_category():
    entry = build_available_object_entry(
        "corner_door_tl",
        {"name": "Corner Door", "item_class": "DoorObjectWall", "placeable": True},
    )
    assert entry is not None
    assert entry["category"] == "door_walls"
    assert entry["item_class"] == "DoorObjectWall"
