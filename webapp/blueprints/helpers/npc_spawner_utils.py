"""Helpers for DM NPC spawner listing, CR grouping, and unique flags."""

from __future__ import annotations

from typing import Any

from natural20.npc import authored_unique_entity_uid, is_unique_npc_properties

_CR_FRACTIONS = {
    0.0: "CR 0",
    0.125: "CR 1/8",
    0.25: "CR 1/4",
    0.5: "CR 1/2",
}

_FRACTION_VALUES = {
    "1/8": 0.125,
    "1/4": 0.25,
    "1/2": 0.5,
    "1/16": 0.0625,
}

_UNRATED_SORT = 10000.0


def parse_npc_cr(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text or text in {"unknown", "unrated", "n/a", "-"}:
        return None
    if text.startswith("cr "):
        text = text[3:].strip()
    if text in _FRACTION_VALUES:
        return _FRACTION_VALUES[text]
    try:
        return float(text)
    except ValueError:
        return None


def format_cr_label(value: Any) -> str:
    numeric = parse_npc_cr(value)
    if numeric is None:
        return "Unrated"
    if numeric in _CR_FRACTIONS:
        return _CR_FRACTIONS[numeric]
    if numeric == int(numeric):
        return f"CR {int(numeric)}"
    return f"CR {numeric}"


def cr_sort_value(value: Any) -> float:
    numeric = parse_npc_cr(value)
    return _UNRATED_SORT if numeric is None else float(numeric)


def build_available_npc_entry(npc) -> dict[str, Any]:
    props = getattr(npc, "properties", None) or {}
    unique = is_unique_npc_properties(props)
    uid = authored_unique_entity_uid(props)
    cr = props.get("cr", "Unknown")
    npc_id = npc.npc_type if hasattr(npc, "npc_type") else props.get("id", "unknown")
    if hasattr(npc, "label"):
        try:
            name = npc.label()
        except TypeError:
            name = npc.label
    else:
        name = props.get("label", getattr(npc, "name", npc_id))
    if hasattr(npc, "token_image"):
        try:
            image = npc.token_image()
        except TypeError:
            image = npc.token_image
    else:
        image = props.get("token", f"{getattr(npc, 'name', npc_id)}.png")
    if hasattr(npc, "armor_class"):
        try:
            ac = npc.armor_class()
        except TypeError:
            ac = npc.armor_class
    else:
        ac = props.get("ac", "Unknown")
    if hasattr(npc, "max_hp"):
        try:
            hp = npc.max_hp()
        except TypeError:
            hp = npc.max_hp
    else:
        hp = props.get("hp", "Unknown")
    return {
        "id": npc_id,
        "name": name,
        "type": props.get("type", "Unknown"),
        "image": image,
        "cr": cr,
        "cr_label": format_cr_label(cr),
        "cr_sort": cr_sort_value(cr),
        "size": props.get("size", "Medium"),
        "ac": ac,
        "hp": hp,
        "unique": bool(unique),
        "entity_uid": uid,
    }


def enrich_npc_spawner_entries(session, entries: list[dict[str, Any]], current_map=None) -> list[dict[str, Any]]:
    """Copy catalog rows and attach live unique-NPC placement on the active map set."""
    map_name = getattr(current_map, "name", None) if current_map is not None else None
    set_id = None
    if session is not None:
        try:
            set_id = session.map_set_for(map_name) if map_name else getattr(session, "active_map_set", None)
        except Exception:
            set_id = getattr(session, "active_map_set", None)

    enriched = []
    for entry in entries:
        item = dict(entry)
        uid = item.get("entity_uid")
        on_map = None
        on_map_pos = None
        if item.get("unique") and uid and session is not None:
            entity = session.entity_by_uid(uid)
            loc = None
            if entity is not None:
                try:
                    loc = session.map_for_entity(entity, map_set=set_id)
                except TypeError:
                    loc = session.map_for_entity(entity)
            if loc is not None:
                on_map = getattr(loc, "name", None)
                try:
                    pos = loc.position_of(entity)
                    if pos is not None:
                        on_map_pos = [int(pos[0]), int(pos[1])]
                except Exception:
                    on_map_pos = None
        item["on_map"] = on_map
        item["on_map_pos"] = on_map_pos
        enriched.append(item)
    return enriched
