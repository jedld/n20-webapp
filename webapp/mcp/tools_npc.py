"""MCP tools that let the NPC LLM query spatial location and area annotation hierarchy.

Two tools are provided:

- ``npc.get_location`` — Return the current map position of an NPC together with
  the enclosing hierarchy of area annotations (most specific -> broadest).
- ``npc.query_area`` — Given an annotation id, return its full details plus the
  hierarchy of child sub-areas contained within it.

These tools are designed for NPC AI to maintain spatial awareness and understand
how places relate to each other (e.g., "behind_bar" is inside "taproom" which is
inside "tavern_ground_floor").
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .context import MCPContext
from .tool_registry import ToolRegistry, tool, tool_error


def _annotation_summary(annotation: Dict[str, Any]) -> Dict[str, Any]:
    """Return a clean, JSON-serialisable summary of one annotation."""
    kind = annotation.get('kind') or 'point'
    summary: Dict[str, Any] = {
        'id': annotation.get('id'),
        'label': annotation.get('label'),
        'kind': kind,
        'description': annotation.get('description'),
    }
    if kind in ('point', 'stack_opening'):
        pos = annotation.get('pos')
        if pos:
            summary['pos'] = [int(pos[0]), int(pos[1])]
    elif kind == 'area':
        bounds = annotation.get('bounds') or {}
        summary['bounds'] = {
            'x1': bounds.get('x1'),
            'y1': bounds.get('y1'),
            'x2': bounds.get('x2'),
            'y2': bounds.get('y2'),
        }
    elif kind == 'polygon':
        pts = annotation.get('points') or []
        summary['points'] = [[int(p[0]), int(p[1])] for p in pts if len(p) >= 2]
    elif kind == 'object_ref':
        summary['entity_uid'] = annotation.get('entity_uid')
    # Detect Magic fields
    if annotation.get('magical'):
        summary['magical'] = True
        summary['magic_school'] = annotation.get('magic_school')
        summary['aura_strength'] = annotation.get('aura_strength')
    return summary


def _contains_point(annotation: Dict[str, Any], x: int, y: int) -> bool:
    """Check if an annotation contains a grid position."""
    from natural20.map_annotations import annotation_contains_point
    return annotation_contains_point(annotation, x, y)


def _contains_annotation(parent: Dict[str, Any], child: Dict[str, Any]) -> bool:
    """Check if parent annotation fully contains child annotation geometry.

    For area/polygon parents, checks if the child's centroid lies within.
    For point parents, exact match.
    """
    from natural20.map_annotations import annotation_centroid

    child_centroid = annotation_centroid(child)
    if child_centroid is None:
        return False

    return _contains_point(parent, int(child_centroid[0]), int(child_centroid[1]))


def _get_annotation_hierarchy(
    battle_map,
    x: int,
    y: int,
) -> List[Dict[str, Any]]:
    """Return annotations enclosing (x, y) ordered most-specific first.

    Uses the same filtering as ``current_location_annotations_at_position``
    but returns the full hierarchy (not filtered to most-specific-only).
    """
    from natural20.map_annotations import (
        annotations_at_position,
        current_location_annotations_at_position,
    )

    # Get location-context annotations (excludes stack_opening, floor_mask)
    location_anns = current_location_annotations_at_position(battle_map.properties, x, y)
    if not location_anns:
        # Fall back to all annotations at position
        location_anns = annotations_at_position(battle_map.properties, x, y)

    # Sort by area size (smallest/most-specific first)
    from natural20.map_annotations import _annotation_area_size
    location_anns.sort(key=lambda a: _annotation_area_size(a))

    return [_annotation_summary(a) for a in location_anns]


def _get_sub_areas(
    battle_map,
    parent_annotation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return all area/polygon annotations whose centroid lies within parent."""
    from natural20.map_annotations import list_map_annotations, _LOCATION_CONTEXT_KINDS

    children: List[Dict[str, Any]] = []
    for ann in list_map_annotations(battle_map.properties):
        if ann.get('id') == parent_annotation.get('id'):
            continue
        if ann.get('kind') not in _LOCATION_CONTEXT_KINDS:
            continue
        if _contains_annotation(parent_annotation, ann):
            children.append(_annotation_summary(ann))

    # Sort by area size (smallest first)
    from natural20.map_annotations import _annotation_area_size
    children.sort(key=lambda a: _annotation_area_size(a))
    return children


def register(registry: ToolRegistry) -> None:
    @tool(
        registry,
        name='npc.get_location',
        description=(
            'Return the current location of an NPC entity including the map name, '
            'grid position, and the enclosing hierarchy of area annotations '
            '(most specific to broadest). Use this to understand where an NPC '
            'is standing and what named areas contain that position. '
            'Example: behind_bar -> taproom -> tavern_ground_floor.'
        ),
        input_schema={
            'type': 'object',
            'required': ['entity_uid'],
            'properties': {
                'entity_uid': {
                    'type': 'string',
                    'description': 'The entity UID of the NPC to query.',
                },
            },
        },
        category='npc',
    )
    def get_location(context: MCPContext, entity_uid: str):
        entity = context.resolve_entity(entity_uid)
        if entity is None:
            raise tool_error(f'Entity not found: {entity_uid}')

        battle_map = context.map_for_entity(entity)
        if battle_map is None:
            raise tool_error(f'Entity {entity_uid} is not on any loaded map')

        # Get position
        try:
            pos = battle_map.entity_or_object_pos(entity)
        except Exception:
            pos = None

        if pos is None:
            return {
                'entity_uid': entity_uid,
                'entity_name': getattr(entity, 'name', None),
                'map': getattr(battle_map, 'name', None),
                'position': None,
                'location_hierarchy': [],
                'note': 'Entity position unavailable',
            }

        x, y = int(pos[0]), int(pos[1])
        hierarchy = _get_annotation_hierarchy(battle_map, x, y)

        return {
            'entity_uid': entity_uid,
            'entity_name': getattr(entity, 'name', None),
            'map': getattr(battle_map, 'name', None),
            'position': [x, y],
            'location_hierarchy': hierarchy,
        }

    @tool(
        registry,
        name='npc.query_area',
        description=(
            'Query a map annotation by id and return its full details plus '
            'the hierarchy of child sub-areas contained within it. '
            'Use this to understand the spatial layout of a named area '
            'and discover nested places inside it. '
            'Example: querying "taproom" returns its bounds and lists '
            'child areas like "behind_bar", "bar_counter", etc.'
        ),
        input_schema={
            'type': 'object',
            'required': ['annotation_id'],
            'properties': {
                'annotation_id': {
                    'type': 'string',
                    'description': 'The id slug of the annotation to query (e.g. "taproom").',
                },
                'map_name': {
                    'type': 'string',
                    'description': (
                        'Map name to search on. Omit to search the default/first map, '
                        'or pass the specific map name.'
                    ),
                },
            },
        },
        category='npc',
    )
    def query_area(context: MCPContext, annotation_id: str, map_name: Optional[str] = None):
        from natural20.map_annotations import normalize_annotation_id

        slug = normalize_annotation_id(annotation_id)
        if not slug:
            raise tool_error(f'Invalid annotation id: {annotation_id!r}')

        # Try to find the annotation on the specified map, then search all maps
        searched_maps: List[str] = []
        parent_annotation: Optional[Dict[str, Any]] = None
        target_map = None

        # Build search order: specified map first, then all maps
        cg = context.current_game
        maps = getattr(cg, 'maps', {}) or {}

        search_order: List[Any] = []
        if map_name:
            named_map = maps.get(map_name)
            if named_map is not None:
                search_order.append(named_map)

        # Add all maps (avoiding duplicates)
        added = {id(m) for m in search_order}
        for m in maps.values():
            if id(m) not in added:
                search_order.append(m)

        for battle_map in search_order:
            map_key = getattr(battle_map, 'name', None) or 'unknown'
            searched_maps.append(map_key)
            ann = battle_map.map_annotation_by_id(slug)
            if ann is not None:
                parent_annotation = ann
                target_map = battle_map
                break

        if parent_annotation is None:
            raise tool_error(
                f'Annotation "{annotation_id}" not found on maps: {", ".join(searched_maps)}'
            )

        # Get child sub-areas
        sub_areas = _get_sub_areas(target_map, parent_annotation)

        return {
            'annotation': _annotation_summary(parent_annotation),
            'map': getattr(target_map, 'name', None),
            'sub_areas': sub_areas,
            'sub_area_count': len(sub_areas),
        }
