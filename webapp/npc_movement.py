"""NPC out-of-combat movement helpers for conversation-mode LLM directives."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from natural20.actions.move_action import MoveAction
from natural20.ai.path_compute import PathCompute
from natural20.entity import Entity
from natural20.session import Session
from natural20.utils.conversation import entity_label, mention_handle_for

logger = logging.getLogger('werkzeug')

SQUARE_COORD_PATTERN = re.compile(
    r'^\s*(-?\d+)\s*[, ]\s*(-?\d+)\s*$'
)


class NpcMovementHelper:
    """Build single-step out-of-combat moves toward entities, objects, or squares."""

    def __init__(self, game_session: Session, current_game):
        self.game_session = game_session
        self.current_game = current_game

    def parse_square_spec(self, raw_value: Optional[str]) -> Optional[Tuple[int, int]]:
        if raw_value is None:
            return None
        match = SQUARE_COORD_PATTERN.match(str(raw_value).strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _actor_known_landmark_ids(self, actor: Entity) -> set[str]:
        from natural20.map_annotations import normalize_annotation_id

        props = getattr(actor, 'properties', None) or {}
        if not isinstance(props, dict):
            props = {}
        raw_known = props.get('known_places') or props.get('known_landmarks') or []
        if not isinstance(raw_known, (list, tuple)):
            return set()
        return {
            normalize_annotation_id(str(item))
            for item in raw_known
            if str(item).strip()
        }

    def should_use_door_navigation(self, actor: Entity, target_info: Optional[Dict[str, Any]]) -> bool:
        if actor is None or not target_info:
            return False
        kind = target_info.get('kind')
        if kind in ('entity', 'object'):
            return True
        if kind != 'annotation':
            return False
        from natural20.map_annotations import normalize_annotation_id

        annotation = target_info.get('annotation') or {}
        ann_id = normalize_annotation_id(str(annotation.get('id') or ''))
        if not ann_id:
            return False
        return ann_id in self._actor_known_landmark_ids(actor)

    def _square_has_blocking_door(
        self,
        battle_map,
        actor: Entity,
        x: int,
        y: int,
        from_pos: Optional[Tuple[int, int]] = None,
    ) -> bool:
        for obj in battle_map.objects_at(x, y):
            if not self._is_door_object(obj):
                continue
            if from_pos is not None and hasattr(obj, 'passable') and obj.passable(from_pos):
                continue
            return True
        return False

    def _resolve_reachable_square(
        self,
        actor: Entity,
        battle_map,
        square: Tuple[int, int],
    ) -> Optional[Tuple[int, int]]:
        """Return a placeable square to path toward, resolving landmarks on doors/walls."""
        dest_x, dest_y = int(square[0]), int(square[1])
        if battle_map.placeable(actor, dest_x, dest_y, squeeze=False):
            if not self._square_has_blocking_door(battle_map, actor, dest_x, dest_y):
                return dest_x, dest_y

        try:
            source_x, source_y = battle_map.position_of(actor)
        except Exception:
            source_x, source_y = dest_x, dest_y

        candidates: List[Tuple[int, int]] = []
        for nx in range(max(0, dest_x - 1), min(battle_map.size[0], dest_x + 2)):
            for ny in range(max(0, dest_y - 1), min(battle_map.size[1], dest_y + 2)):
                if (nx, ny) == (dest_x, dest_y):
                    continue
                if abs(nx - dest_x) + abs(ny - dest_y) != 1:
                    continue
                if battle_map.placeable(actor, nx, ny, squeeze=False):
                    candidates.append((nx, ny))

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda pos: abs(pos[0] - source_x) + abs(pos[1] - source_y),
        )

    def _annotation_home_map(self, target_info: Dict[str, Any]):
        return target_info.get('annotation_map')

    def _immediate_landmark_square(
        self,
        actor: Entity,
        target_info: Dict[str, Any],
    ) -> Optional[Tuple[int, int]]:
        """Return the next square to path toward on the actor's current map."""
        if target_info.get('kind') != 'annotation':
            return None

        from natural20.map_annotations import annotation_anchor_square

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        annotation = target_info.get('annotation') or {}
        annotation_map = self._annotation_home_map(target_info) or battle_map
        dest_square = annotation_anchor_square(annotation, annotation_map)
        if dest_square is None:
            return None

        dest_x, dest_y = int(dest_square[0]), int(dest_square[1])
        if annotation_map is battle_map:
            return self._resolve_reachable_square(actor, battle_map, (dest_x, dest_y))

        try:
            source_x, source_y = battle_map.position_of(actor)
        except Exception:
            return None

        path_compute = PathCompute(None, battle_map, actor, ignore_opposing=True)
        door_navigation = self.should_use_door_navigation(actor, target_info)
        plan = path_compute.compute_cross_map_path(
            battle_map,
            int(source_x),
            int(source_y),
            annotation_map,
            dest_x,
            dest_y,
            door_navigation=door_navigation,
        )
        if not plan:
            return None
        segment_path = plan[0].get('path') or []
        if not segment_path:
            return None
        portal_x, portal_y = int(segment_path[-1][0]), int(segment_path[-1][1])
        return self._resolve_reachable_square(actor, battle_map, (portal_x, portal_y))

    def _destination_square(self, actor: Entity, target_info: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        kind = target_info.get('kind')
        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        if kind == 'square':
            square = target_info.get('square')
            if not square:
                return None
            return int(square[0]), int(square[1])

        if kind == 'annotation':
            return self._immediate_landmark_square(actor, target_info)

        target = target_info.get('target')
        if target is None:
            return None
        target_pos = battle_map.entity_or_object_pos(target)
        if target_pos is None:
            return None
        return int(target_pos[0]), int(target_pos[1])

    def _is_door_object(self, obj) -> bool:
        from natural20.item_library.door_object import DoorObject, DoorObjectWall

        if isinstance(obj, (DoorObject, DoorObjectWall)):
            return True
        kind_of_door = getattr(obj, 'kind_of_door', None)
        return callable(kind_of_door) and bool(kind_of_door())

    def _describe_blocking_door(
        self,
        actor: Entity,
        battle_map,
        door_obj,
        door_pos: Tuple[int, int],
        from_pos: Tuple[int, int],
    ) -> Dict[str, Any]:
        interactions: Dict[str, Any] = {}
        try:
            interactions = door_obj.available_interactions(actor, battle=None) or {}
        except Exception:
            interactions = {}

        enabled_actions = [
            interaction_name
            for interaction_name, details in interactions.items()
            if not (details or {}).get('disabled')
        ]

        state_parts: List[str] = []
        if hasattr(door_obj, 'opened') and hasattr(door_obj, 'closed'):
            state_parts.append('open' if door_obj.opened() else 'closed')
        if hasattr(door_obj, 'locked'):
            state_parts.append('locked' if bool(getattr(door_obj, 'locked', False)) else 'unlocked')

        label = (
            getattr(door_obj, 'name', None)
            or (door_obj.label() if callable(getattr(door_obj, 'label', None)) else None)
            or 'door'
        )
        entity_uid = str(getattr(door_obj, 'entity_uid', '') or '')

        return {
            'object': door_obj,
            'position': [int(door_pos[0]), int(door_pos[1])],
            'from_position': [int(from_pos[0]), int(from_pos[1])],
            'label': str(label),
            'entity_uid': entity_uid,
            'state': ', '.join(state_parts) or 'unknown',
            'available_actions': enabled_actions,
            'mention': mention_handle_for(door_obj) if entity_uid else str(label),
        }

    def _adjacent_blocking_door(
        self,
        actor: Entity,
        battle_map,
        from_pos: Tuple[int, int],
    ) -> Optional[Dict[str, Any]]:
        fx, fy = int(from_pos[0]), int(from_pos[1])
        for nx in range(max(0, fx - 1), min(battle_map.size[0], fx + 2)):
            for ny in range(max(0, fy - 1), min(battle_map.size[1], fy + 2)):
                if (nx, ny) == (fx, fy):
                    continue
                if abs(nx - fx) + abs(ny - fy) > 1:
                    continue
                if battle_map.bidirectionally_passable(
                    actor,
                    nx,
                    ny,
                    (fx, fy),
                    None,
                    False,
                    ignore_opposing=True,
                ):
                    continue
                for obj in battle_map.objects_at(nx, ny):
                    if not self._is_door_object(obj):
                        continue
                    if hasattr(obj, 'passable') and obj.passable((fx, fy)):
                        continue
                    return self._describe_blocking_door(actor, battle_map, obj, (nx, ny), (fx, fy))
        return None

    def detect_blocking_door_on_route(
        self,
        actor: Entity,
        target_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Return door details when the route to the target is blocked at the actor's tile."""
        if actor is None or not target_info:
            return None
        if not self.should_use_door_navigation(actor, target_info):
            return None

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        destination = self._destination_square(actor, target_info)
        if destination is None:
            return None

        try:
            source_x, source_y = battle_map.position_of(actor)
        except Exception:
            return None

        source = (int(source_x), int(source_y))
        if source == destination:
            return None

        path_compute = PathCompute(None, battle_map, actor, ignore_opposing=True)
        path = path_compute.compute_path(
            source[0],
            source[1],
            destination[0],
            destination[1],
            door_navigation=True,
        )
        if not path or path[-1] == destination:
            return None
        if path[-1] != source:
            return None

        return self._adjacent_blocking_door(actor, battle_map, source)

    def serialize_route_blocker(self, blocker: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not blocker:
            return None
        return {
            'position': blocker.get('position'),
            'from_position': blocker.get('from_position'),
            'label': blocker.get('label'),
            'entity_uid': blocker.get('entity_uid'),
            'state': blocker.get('state'),
            'available_actions': list(blocker.get('available_actions') or []),
            'mention': blocker.get('mention'),
        }

    def describe_target(self, target_info: Optional[Dict[str, Any]]) -> str:
        if not target_info:
            return 'unknown'
        kind = target_info.get('kind')
        if kind == 'square':
            square = target_info.get('square') or (None, None)
            return f'square ({square[0]}, {square[1]})'
        if kind == 'annotation':
            annotation = target_info.get('annotation') or {}
            return str(annotation.get('label') or annotation.get('id') or 'landmark')
        target = target_info.get('target')
        if target is None:
            return target_info.get('target_spec') or 'unknown'
        return entity_label(target)

    def movement_target_viable(self, actor: Entity, target_info: Dict[str, Any]) -> Tuple[bool, str]:
        """Return whether the movement target can be pursued from the actor's current map."""
        if actor is None or not target_info:
            return False, 'Missing actor or movement target'

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return False, 'Actor is not on a map'

        kind = target_info.get('kind')
        if kind in ('square', 'annotation'):
            if kind == 'annotation':
                annotation = target_info.get('annotation') or {}
                annotation_map = self._annotation_home_map(target_info) or battle_map
                from natural20.map_annotations import annotation_anchor_square

                dest = annotation_anchor_square(annotation, annotation_map)
                if dest is None:
                    spec = target_info.get('target_spec') or annotation.get('id') or 'landmark'
                    return False, f'Unknown landmark: {spec}'
                if annotation_map is not battle_map:
                    try:
                        source_x, source_y = battle_map.position_of(actor)
                    except Exception:
                        return False, 'Cannot determine position for cross-map routing'
                    plan = PathCompute(None, battle_map, actor, ignore_opposing=True).compute_cross_map_path(
                        battle_map,
                        int(source_x),
                        int(source_y),
                        annotation_map,
                        int(dest[0]),
                        int(dest[1]),
                        door_navigation=self.should_use_door_navigation(actor, target_info),
                    )
                    if not plan:
                        map_name = getattr(annotation_map, 'name', 'another map')
                        return False, f'No route to landmark on {map_name}'
            return True, ''

        target = target_info.get('target')
        if target is None:
            return False, 'Movement target missing'

        target_map = self.current_game.get_map_for_entity(target)
        if target_map is None:
            return False, f'{entity_label(target)} is not on the map'
        if target_map is not battle_map:
            try:
                source_x, source_y = battle_map.position_of(actor)
                target_pos = target_map.position_of(target)
            except Exception:
                return False, 'Cannot determine positions for cross-map follow'
            plan = PathCompute(None, battle_map, actor, ignore_opposing=True).compute_cross_map_path(
                battle_map,
                int(source_x),
                int(source_y),
                target_map,
                int(target_pos[0]),
                int(target_pos[1]),
                door_navigation=True,
            )
            if not plan:
                actor_map_name = getattr(battle_map, 'name', 'unknown')
                target_map_name = getattr(target_map, 'name', 'unknown')
                return (
                    False,
                    f'No route to {entity_label(target)} on {target_map_name} (actor on {actor_map_name})',
                )
            return True, ''

        try:
            battle_map.distance(actor, target)
        except Exception:
            return False, f'Cannot path toward {entity_label(target)}'

        return True, ''

    def movement_reached(self, actor: Entity, target_info: Dict[str, Any], distance_ft: int = 5) -> bool:
        if actor is None or not target_info:
            return True

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return True

        kind = target_info.get('kind')
        feet_per_grid = getattr(battle_map, 'feet_per_grid', 5) or 5

        if kind == 'square':
            square = target_info.get('square')
            if not square:
                return True
            try:
                pos = battle_map.position_of(actor)
            except Exception:
                return False
            return tuple(pos) == tuple(square)

        if kind == 'annotation':
            from natural20.map_annotations import annotation_anchor_square, annotation_contains_point

            annotation = target_info.get('annotation') or {}
            annotation_map = self._annotation_home_map(target_info) or battle_map
            if annotation_map is not battle_map:
                return False
            ann_kind = annotation.get('kind')
            try:
                pos = battle_map.position_of(actor)
            except Exception:
                return False
            if ann_kind in {'area', 'polygon'}:
                return annotation_contains_point(annotation, int(pos[0]), int(pos[1]))
            if ann_kind == 'object_ref':
                uid = annotation.get('entity_uid')
                obj = annotation_map.object_by_uid(uid) or annotation_map.entity_by_uid(uid)
                if obj is None:
                    return False
                try:
                    current_distance_ft = battle_map.distance(actor, obj) * feet_per_grid
                except Exception:
                    return False
                return current_distance_ft <= max(0, int(distance_ft or 0))
            anchor = annotation.get('pos')
            if anchor:
                ax, ay = int(anchor[0]), int(anchor[1])
                if (int(pos[0]), int(pos[1])) == (ax, ay):
                    return True
                if not battle_map.placeable(actor, ax, ay, squeeze=False):
                    resolved = self._resolve_reachable_square(actor, annotation_map, (ax, ay))
                    if resolved and (int(pos[0]), int(pos[1])) == tuple(resolved):
                        for obj in annotation_map.objects_at(ax, ay):
                            if self._is_door_object(obj) and hasattr(obj, 'passable') and obj.passable((pos[0], pos[1])):
                                return True
                        return True
            return False

        target = target_info.get('target')
        if target is None:
            return True

        target_map = self.current_game.get_map_for_entity(target)
        if target_map is not None and target_map is not battle_map:
            return False

        try:
            current_distance_ft = battle_map.distance(actor, target) * feet_per_grid
        except Exception:
            return False
        return current_distance_ft <= max(0, int(distance_ft or 0))

    def build_move_step(
        self,
        actor: Entity,
        target_info: Dict[str, Any],
        distance_ft: int = 5,
    ):
        if actor is None or not target_info:
            return None

        kind = target_info.get('kind')
        if kind == 'square':
            return self._build_move_to_square(actor, target_info.get('square'))
        if kind == 'annotation':
            from natural20.map_annotations import annotation_anchor_square

            battle_map = self.current_game.get_map_for_entity(actor)
            if battle_map is None:
                return None
            annotation = target_info.get('annotation') or {}
            if annotation.get('kind') == 'object_ref':
                annotation_map = self._annotation_home_map(target_info) or battle_map
                uid = annotation.get('entity_uid')
                obj = annotation_map.object_by_uid(uid) or annotation_map.entity_by_uid(uid)
                if obj is not None:
                    return self._build_move_toward_target(
                        actor,
                        obj,
                        distance_ft=distance_ft,
                        door_navigation=self.should_use_door_navigation(actor, target_info),
                    )
            square = self._immediate_landmark_square(actor, target_info)
            return self._build_move_to_square(
                actor,
                square,
                door_navigation=self.should_use_door_navigation(actor, target_info),
                battle_map=battle_map,
            )
        if kind in ('entity', 'object'):
            # Entity follow/approach often routes through doors (stairs, guest rooms).
            entity_door_nav = True
            return self._build_move_toward_target(
                actor,
                target_info.get('target'),
                distance_ft=distance_ft,
                door_navigation=entity_door_nav,
            )
        return None

    def _greedy_step_toward(
        self,
        actor: Entity,
        battle_map,
        source: Tuple[int, int],
        destination: Tuple[int, int],
    ) -> Optional[List[Tuple[int, int]]]:
        sx, sy = int(source[0]), int(source[1])
        dx, dy = int(destination[0]), int(destination[1])
        if (sx, sy) == (dx, dy):
            return None

        source_distance = abs(sx - dx) + abs(sy - dy)
        best_step = None
        best_distance = source_distance
        for nx in range(max(0, sx - 1), min(battle_map.size[0], sx + 2)):
            for ny in range(max(0, sy - 1), min(battle_map.size[1], sy + 2)):
                if (nx, ny) == (sx, sy):
                    continue
                if abs(nx - sx) + abs(ny - sy) != 1:
                    continue
                if not battle_map.bidirectionally_passable(
                    actor,
                    nx,
                    ny,
                    (sx, sy),
                    None,
                    False,
                    ignore_opposing=True,
                ):
                    continue
                candidate_distance = abs(nx - dx) + abs(ny - dy)
                if candidate_distance < best_distance:
                    best_distance = candidate_distance
                    best_step = (nx, ny)

        if best_step is None:
            return None
        return [(sx, sy), best_step]

    def _build_move_to_square(
        self,
        actor: Entity,
        square: Optional[Tuple[int, int]],
        *,
        door_navigation: bool = False,
        battle_map=None,
    ):
        if actor is None or square is None:
            return None

        if battle_map is None:
            battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        dest_x, dest_y = int(square[0]), int(square[1])
        try:
            source_x, source_y = battle_map.position_of(actor)
        except Exception:
            return None

        if (source_x, source_y) == (dest_x, dest_y):
            return None

        resolved = self._resolve_reachable_square(actor, battle_map, (dest_x, dest_y))
        if resolved is None:
            return None
        dest_x, dest_y = resolved

        if (source_x, source_y) == (dest_x, dest_y):
            return None

        path_compute = PathCompute(None, battle_map, actor, ignore_opposing=True)
        trimmed_path = path_compute.compute_path(
            source_x,
            source_y,
            dest_x,
            dest_y,
            available_movement_cost=actor.available_movement(None),
            door_navigation=False,
        )
        if door_navigation:
            if not trimmed_path or len(trimmed_path) < 2:
                door_path = path_compute.compute_path(
                    source_x,
                    source_y,
                    dest_x,
                    dest_y,
                    available_movement_cost=actor.available_movement(None),
                    door_navigation=True,
                )
                if door_path and len(door_path) >= 2:
                    trimmed_path = door_path
                else:
                    trimmed_path = self._greedy_step_toward(
                        actor,
                        battle_map,
                        (source_x, source_y),
                        (dest_x, dest_y),
                    )
        elif not trimmed_path or len(trimmed_path) < 2:
            trimmed_path = path_compute.compute_path(
                source_x,
                source_y,
                dest_x,
                dest_y,
                available_movement_cost=actor.available_movement(None),
                door_navigation=True,
            )
        if not trimmed_path or len(trimmed_path) < 2:
            return None

        return self._finalize_move_action(actor, trimmed_path)

    def _immediate_entity_square(
        self,
        actor: Entity,
        target,
        *,
        door_navigation: bool = False,
    ) -> Optional[Tuple[int, int]]:
        """Return the next square to path toward on the actor's current map."""
        battle_map = self.current_game.get_map_for_entity(actor)
        target_map = self.current_game.get_map_for_entity(target)
        if battle_map is None or target_map is None:
            return None

        try:
            target_pos = target_map.position_of(target)
        except Exception:
            return None

        dest_x, dest_y = int(target_pos[0]), int(target_pos[1])
        if target_map is battle_map:
            return self._resolve_reachable_square(actor, battle_map, (dest_x, dest_y))

        try:
            source_x, source_y = battle_map.position_of(actor)
        except Exception:
            return None

        path_compute = PathCompute(None, battle_map, actor, ignore_opposing=True)
        plan = path_compute.compute_cross_map_path(
            battle_map,
            int(source_x),
            int(source_y),
            target_map,
            dest_x,
            dest_y,
            door_navigation=True,
        )
        if not plan:
            return None
        segment_path = plan[0].get('path') or []
        if not segment_path:
            return None
        portal_x, portal_y = int(segment_path[-1][0]), int(segment_path[-1][1])
        return self._resolve_reachable_square(actor, battle_map, (portal_x, portal_y))

    def _build_move_toward_target(
        self,
        actor: Entity,
        target,
        distance_ft: int = 5,
        *,
        door_navigation: bool = False,
    ):
        if actor is None or target is None:
            return None

        battle_map = self.current_game.get_map_for_entity(actor)
        if battle_map is None:
            return None

        target_map = self.current_game.get_map_for_entity(target)
        feet_per_grid = getattr(battle_map, 'feet_per_grid', 5) or 5
        target_pos = battle_map.entity_or_object_pos(target)
        if target_pos is None and target_map is not None and target_map is not battle_map:
            portal_square = self._immediate_entity_square(
                actor,
                target,
                door_navigation=door_navigation,
            )
            if portal_square is None:
                return None
            return self._build_move_to_square(
                actor,
                portal_square,
                door_navigation=door_navigation,
                battle_map=battle_map,
            )
        if target_pos is None:
            return None

        current_distance_ft = battle_map.distance(actor, target) * feet_per_grid
        if current_distance_ft <= distance_ft:
            return None

        candidate_positions = []
        for pos_x in range(battle_map.size[0]):
            for pos_y in range(battle_map.size[1]):
                if not battle_map.placeable(actor, pos_x, pos_y, squeeze=False):
                    continue
                try:
                    candidate_distance_ft = battle_map.distance(
                        actor,
                        target,
                        entity_1_pos=(pos_x, pos_y),
                        entity_2_pos=target_pos,
                    ) * feet_per_grid
                except Exception:
                    continue
                if candidate_distance_ft <= distance_ft:
                    candidate_positions.append((pos_x, pos_y))

        if not candidate_positions:
            return None

        source_x, source_y = battle_map.position_of(actor)
        path_compute = PathCompute(None, battle_map, actor, ignore_opposing=True)
        paths = path_compute.compute_paths_to_multiple_destinations(
            source_x,
            source_y,
            candidate_positions,
            door_navigation=door_navigation,
        )

        best_destination = None
        best_path = None
        for destination, path in paths.items():
            if not path or len(path) < 2:
                continue
            if best_path is None or len(path) < len(best_path):
                best_destination = destination
                best_path = path

        if best_destination is None or best_path is None:
            if door_navigation:
                return None
            paths = path_compute.compute_paths_to_multiple_destinations(
                source_x,
                source_y,
                candidate_positions,
                door_navigation=True,
            )
            for destination, path in paths.items():
                if not path or len(path) < 2:
                    continue
                if best_path is None or len(path) < len(best_path):
                    best_destination = destination
                    best_path = path

        if best_destination is None or best_path is None:
            if door_navigation:
                trimmed_path = self._greedy_step_toward(
                    actor,
                    battle_map,
                    (source_x, source_y),
                    target_pos,
                )
                if trimmed_path and len(trimmed_path) >= 2:
                    return self._finalize_move_action(actor, trimmed_path)
            return None

        trimmed_path = path_compute.compute_path(
            source_x,
            source_y,
            best_destination[0],
            best_destination[1],
            available_movement_cost=actor.available_movement(None),
            door_navigation=door_navigation,
        )
        if (not trimmed_path or len(trimmed_path) < 2) and not door_navigation:
            trimmed_path = path_compute.compute_path(
                source_x,
                source_y,
                best_destination[0],
                best_destination[1],
                available_movement_cost=actor.available_movement(None),
                door_navigation=True,
            )
        if (not trimmed_path or len(trimmed_path) < 2) and door_navigation:
            trimmed_path = self._greedy_step_toward(
                actor,
                battle_map,
                (source_x, source_y),
                best_destination,
            )
        if not trimmed_path or len(trimmed_path) < 2:
            return None

        return self._finalize_move_action(actor, trimmed_path)

    def _finalize_move_action(self, actor: Entity, trimmed_path):
        if not trimmed_path or len(trimmed_path) < 2:
            return None
        action = MoveAction(self.game_session, actor, 'move')
        action.move_path = [
            list(trimmed_path[0]),
            list(trimmed_path[1]),
        ]
        return action

    def movement_snapshot(self, actor: Entity, task: Dict[str, Any]) -> str:
        battle_map = self.current_game.get_map_for_entity(actor)
        position = None
        if battle_map is not None:
            try:
                position = battle_map.position_of(actor)
            except Exception:
                position = None

        target_info = task.get('target_info') or {}
        target_label = self.describe_target(target_info)
        reached = self.movement_reached(actor, target_info, distance_ft=task.get('distance_ft', 5))

        lines = [
            f"Movement mode: {task.get('mode', 'move_to')}",
            f"Target: {target_label}",
            f"Maintain distance (ft): {task.get('distance_ft', 5)}",
            f"Your position: {position}",
            f"Destination reached: {reached}",
            f"Game time: {self.game_session.game_time}",
        ]

        route_blocker = task.get('route_blocker')
        if route_blocker:
            lines.append(
                "A door blocks your route to the landmark: "
                f"{route_blocker.get('label')} at {route_blocker.get('position')} "
                f"({route_blocker.get('state')})."
            )
            if route_blocker.get('mention'):
                lines.append(f"Door reference: {route_blocker.get('mention')}")
            actions = route_blocker.get('available_actions') or []
            if actions:
                lines.append(f"Available door interactions: {', '.join(actions)}")
            lines.append(
                "Use [INTERACT: target=<door name or @handle>, action=open|unlock|lockpick] "
                "to open or unlock the door, then movement will continue on the next tick."
            )

        if task.get('mode') == 'follow':
            target_uid = task.get('target_uid')
            follow_target = None
            if target_uid:
                try:
                    follow_target = self.game_session.entity_by_uid(target_uid)
                except Exception:
                    follow_target = None
            if follow_target is not None and battle_map is not None:
                try:
                    target_pos = battle_map.position_of(follow_target)
                    lines.append(f"Follow target position: {target_pos}")
                except Exception:
                    pass

        lines.append(
            "You may continue with [MOVE: ...] or [FOLLOW: ...], or stop with "
            "[STOP_MOVE] / [STOP_FOLLOW]. Return only control tags plus an optional spoken line."
        )
        try:
            from webapp.blueprints.helpers.runtime_state import get_entity_rag_handler
            rag_handler = get_entity_rag_handler()
            if rag_handler is not None:
                extra = rag_handler.build_movement_situational_context(actor, task)
                if extra:
                    lines.append(extra)
        except Exception:
            pass
        return '\n'.join(lines)


class NpcMovementManager:
    """Schedules and executes NPC movement/follow tasks outside combat."""

    def __init__(self, manager):
        self.manager = manager

    def exploration_movement_blocked(self, entity) -> bool:
        """Return True when this entity must not take out-of-combat movement steps."""
        battle = self.manager.get_current_battle()
        if battle is None:
            return False
        if entity is None:
            return True
        try:
            return entity in battle.entities
        except Exception:
            return True

    def _task_id(self, entity) -> str:
        return str(getattr(entity, 'entity_uid', '') or '')

    def get_task(self, entity) -> Optional[Dict[str, Any]]:
        if entity is None:
            return None
        return self.manager.npc_movements.get(self._task_id(entity))

    def cancel(self, entity, reason: Optional[str] = None, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if entity is None:
            return None
        task_id = self._task_id(entity)
        with self.manager.game_state_lock:
            task = self.manager.npc_movements.get(task_id)
            if task is None:
                return None
            if mode and task.get('mode') != mode:
                return task
            task['status'] = 'cancelled'
            task['running'] = False
            task['next_run_at'] = None
            task['last_error'] = reason
            task['updated_at_game_time'] = self.manager.game_session.game_time
            if reason:
                history = task.setdefault('history', [])
                history.append({
                    'time': self.manager.game_session.game_time,
                    'event': 'cancelled',
                    'reason': reason,
                })
                task['history'] = history[-12:]
        return task

    def schedule_move_to(
        self,
        entity,
        target_info: Dict[str, Any],
        distance_ft: int = 5,
        speaker=None,
        *,
        return_to_post: bool = False,
        trigger_arrival_debrief: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if entity is None or not target_info:
            return None

        task_id = self._task_id(entity)
        requester_uid = getattr(speaker, 'entity_uid', None) if speaker is not None else None
        now = time.time()
        task_record = {
            'entity_uid': task_id,
            'mode': 'move_to',
            'target_info': dict(target_info),
            'target_uid': self._target_uid_from_info(target_info),
            'distance_ft': max(0, int(distance_ft or 0)),
            'status': 'active',
            'created_at_game_time': self.manager.game_session.game_time,
            'updated_at_game_time': self.manager.game_session.game_time,
            'next_run_at': now,
            'last_follow_at': 0.0,
            'attempts': 0,
            'history': [],
            'requester_uid': requester_uid,
            'return_to_post': bool(return_to_post),
            'trigger_arrival_debrief': bool(trigger_arrival_debrief),
            'last_error': None,
            'running': False,
        }
        with self.manager.game_state_lock:
            self.manager.npc_movements[task_id] = task_record
        self.kick(entity)
        return task_record

    def kick(self, entity=None) -> None:
        """Mark movement task(s) due immediately so the worker picks them up."""
        now = time.time()
        task_id = self._task_id(entity) if entity is not None else None
        with self.manager.game_state_lock:
            for uid, task in self.manager.npc_movements.items():
                if task_id is not None and uid != task_id:
                    continue
                if task.get('status') == 'active':
                    task['next_run_at'] = now

    def schedule_follow(
        self,
        entity,
        target,
        distance_ft: int = 5,
        speaker=None,
    ) -> Optional[Dict[str, Any]]:
        if entity is None or target is None:
            return None

        target_info = {
            'kind': 'entity',
            'target': target,
            'target_spec': mention_handle_for(target),
        }
        task_id = self._task_id(entity)
        requester_uid = getattr(speaker, 'entity_uid', None) if speaker is not None else None
        now = time.time()
        task_record = {
            'entity_uid': task_id,
            'mode': 'follow',
            'target_info': target_info,
            'target_uid': str(getattr(target, 'entity_uid', '') or ''),
            'distance_ft': max(0, int(distance_ft or 5)),
            'status': 'active',
            'created_at_game_time': self.manager.game_session.game_time,
            'updated_at_game_time': self.manager.game_session.game_time,
            'next_run_at': now,
            'last_follow_at': 0.0,
            'attempts': 0,
            'history': [],
            'requester_uid': requester_uid,
            'last_error': None,
            'running': False,
        }
        with self.manager.game_state_lock:
            self.manager.npc_movements[task_id] = task_record
        self.kick(entity)
        return task_record

    def record_history(self, entity, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if entity is None:
            return None
        task = self.manager.npc_movements.get(self._task_id(entity))
        if task is None:
            return None
        task['history'].append(entry)
        task['history'] = task['history'][-12:]
        task['updated_at_game_time'] = self.manager.game_session.game_time
        return task

    def complete(self, entity, status: str = 'completed', reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if entity is None:
            return None
        task_id = self._task_id(entity)
        with self.manager.game_state_lock:
            task = self.manager.npc_movements.get(task_id)
            if task is None:
                return None
            task['status'] = status
            task['running'] = False
            task['next_run_at'] = None
            task['last_error'] = reason
            task['updated_at_game_time'] = self.manager.game_session.game_time
            if reason:
                task['history'].append({
                    'time': self.manager.game_session.game_time,
                    'event': status,
                    'reason': reason,
                })
                task['history'] = task['history'][-12:]
        return task

    def _target_uid_from_info(self, target_info: Dict[str, Any]) -> Optional[str]:
        if not target_info:
            return None
        if target_info.get('kind') == 'square':
            square = target_info.get('square')
            if square:
                return f"square:{square[0]},{square[1]}"
            return None
        if target_info.get('kind') == 'annotation':
            annotation = target_info.get('annotation') or {}
            ann_id = annotation.get('id')
            return f"landmark:{ann_id}" if ann_id else None
        target = target_info.get('target')
        if target is None:
            return None
        return str(getattr(target, 'entity_uid', '') or '') or None

    def movement_worker(self):
        while not self.manager._movement_thread_stop.is_set():
            if not self.manager.npc_movements:
                self.manager._movement_thread_stop.wait(self.manager.goal_poll_interval)
                continue
            if getattr(self.manager, 'realtime_ui_suppressed', lambda: False)():
                self.manager._movement_thread_stop.wait(self.manager.goal_poll_interval)
                continue

            due_task_ids = []
            now = time.time()
            with self.manager.game_state_lock:
                for task_id, task in self.manager.npc_movements.items():
                    if task.get('status') != 'active':
                        continue
                    if task.get('running'):
                        continue
                    entity = self.manager.get_entity_by_uid(task_id)
                    if self.exploration_movement_blocked(entity):
                        continue
                    next_run_at = task.get('next_run_at')
                    if next_run_at is None or next_run_at > now:
                        continue
                    task['running'] = True
                    due_task_ids.append(task_id)

            for task_id in due_task_ids:
                try:
                    from webapp.blueprints.helpers.runtime_state import (
                        get_entity_rag_handler,
                        get_llm_conversation_handler,
                    )
                    rag_handler = get_entity_rag_handler()
                    llm_handler = get_llm_conversation_handler()
                    if rag_handler is not None:
                        rag_handler.execute_scheduled_movement(
                            task_id,
                            llm_handler,
                            allow_movement_llm=llm_handler is not None,
                        )
                except Exception as exc:
                    self.manager.logger.error(
                        "Failed to execute NPC movement for %s: %s",
                        task_id,
                        exc,
                    )
                    entity = self.manager.get_entity_by_uid(task_id)
                    if entity is not None:
                        self.record_history(entity, {
                            'time': self.manager.game_session.game_time,
                            'event': 'error',
                            'reason': str(exc),
                        })
                finally:
                    with self.manager.game_state_lock:
                        task = self.manager.npc_movements.get(task_id)
                        if task is None:
                            continue
                        task['running'] = False
                        if task.get('status') == 'active':
                            task['attempts'] = task.get('attempts', 0) + 1
                            task['next_run_at'] = time.time() + self.manager.goal_turn_seconds
                            task['updated_at_game_time'] = self.manager.game_session.game_time

            self.manager._movement_thread_stop.wait(self.manager.goal_poll_interval)

    def on_entity_moved(self, entity):
        if entity is None:
            return

        moved_uid = str(getattr(entity, 'entity_uid', '') or '')
        if not moved_uid:
            return

        follower_ids = []
        with self.manager.game_state_lock:
            for task_id, task in self.manager.npc_movements.items():
                if task.get('status') != 'active':
                    continue
                if task.get('mode') != 'follow':
                    continue
                if task.get('target_uid') != moved_uid:
                    continue
                if task.get('running'):
                    continue
                follower = self.manager.get_entity_by_uid(task_id)
                if self.exploration_movement_blocked(follower):
                    continue
                task['running'] = True
                follower_ids.append(task_id)

        for task_id in follower_ids:
            try:
                from webapp.blueprints.helpers.runtime_state import (
                    get_entity_rag_handler,
                    get_llm_conversation_handler,
                )
                rag_handler = get_entity_rag_handler()
                llm_handler = get_llm_conversation_handler()
                if rag_handler is not None:
                    rag_handler.execute_scheduled_movement(
                        task_id,
                        llm_handler,
                        triggered_by_follow=True,
                        allow_movement_llm=llm_handler is not None,
                    )
            except Exception as exc:
                self.manager.logger.error(
                    "Failed follow-step for %s after %s moved: %s",
                    task_id,
                    moved_uid,
                    exc,
                )
                follower = self.manager.get_entity_by_uid(task_id)
                if follower is not None:
                    self.record_history(follower, {
                        'time': self.manager.game_session.game_time,
                        'event': 'error',
                        'reason': str(exc),
                    })
            finally:
                with self.manager.game_state_lock:
                    task = self.manager.npc_movements.get(task_id)
                    if task is None:
                        continue
                    task['running'] = False
                    if task.get('status') == 'active':
                        task['last_follow_at'] = time.time()
