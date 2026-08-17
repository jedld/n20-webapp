"""Tests for NPC spawner CR grouping and unique flags."""

from __future__ import annotations

from types import SimpleNamespace

from natural20.npc import is_unique_npc_properties
from webapp.blueprints.helpers.npc_spawner_utils import (
    build_available_npc_entry,
    cr_sort_value,
    enrich_npc_spawner_entries,
    format_cr_label,
    parse_npc_cr,
)


def test_format_cr_label_uses_fractions_and_unrated():
    assert format_cr_label(0.125) == "CR 1/8"
    assert format_cr_label("1/4") == "CR 1/4"
    assert format_cr_label(2) == "CR 2"
    assert format_cr_label("Unknown") == "Unrated"
    assert parse_npc_cr("cr 1/2") == 0.5
    assert cr_sort_value("Unknown") > cr_sort_value(30)


def test_build_available_npc_entry_marks_unique_from_entity_uid():
    npc = SimpleNamespace(
        npc_type="ireena_kolyana",
        name="Ireena",
        properties={"entity_uid": "npc_ireena", "cr": 0.125, "size": "medium", "type": "humanoid"},
        label=lambda: "Ireena",
        token_image=lambda: "token_ireena.png",
        armor_class=lambda: 15,
        max_hp=lambda: 14,
    )
    entry = build_available_npc_entry(npc)
    assert entry["unique"] is True
    assert entry["entity_uid"] == "npc_ireena"
    assert entry["cr_label"] == "CR 1/8"
    assert is_unique_npc_properties(npc.properties) is True


def test_build_available_npc_entry_generic_monster_is_not_unique():
    npc = SimpleNamespace(
        npc_type="goblin",
        name="Goblin",
        properties={"uid": "npc_goblin", "cr": 0.25, "size": "small"},
        label=lambda: "Goblin",
        token_image=lambda: "goblin.png",
        armor_class=lambda: 15,
        max_hp=lambda: 7,
    )
    entry = build_available_npc_entry(npc)
    assert entry["unique"] is False
    assert entry["entity_uid"] is None


def test_enrich_npc_spawner_entries_sets_on_map_for_active_set():
    entity = SimpleNamespace(entity_uid="npc_ireena")
    battle_map = SimpleNamespace(name="tavern", position_of=lambda ent: [3, 4])
    session = SimpleNamespace(
        map_set_for=lambda name: "root",
        active_map_set="root",
        entity_by_uid=lambda uid: entity if uid == "npc_ireena" else None,
        map_for_entity=lambda ent, map_set=None: battle_map if ent is entity else None,
    )
    rows = enrich_npc_spawner_entries(
        session,
        [{"id": "ireena_kolyana", "unique": True, "entity_uid": "npc_ireena", "name": "Ireena"}],
        current_map=battle_map,
    )
    assert rows[0]["on_map"] == "tavern"
    assert rows[0]["on_map_pos"] == [3, 4]
