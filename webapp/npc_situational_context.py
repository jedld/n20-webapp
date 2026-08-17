"""Situational awareness blocks for NPC movement and goal LLM prompts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from natural20.entity import Entity
from natural20.player_character import PlayerCharacter
from natural20.utils.conversation import entity_label, mention_handle_for
from natural20.map_annotations import (
    current_location_annotations_at_position,
    format_current_location_for_llm,
    format_location_hierarchy_for_llm,
)
from natural20.utils.conversation_offers import (
    OFFER_STATE_KEY,
    has_completed_item_offer,
    merged_offer_configs,
)


def _inventory_qty(entity: Entity, item_slug: str) -> int:
    try:
        return int((getattr(entity, 'inventory', {}) or {}).get(item_slug, {}).get('qty') or 0)
    except (TypeError, ValueError):
        return 0


def _door_state_lines(actor: Entity, door_obj, battle_map) -> List[str]:
    if door_obj is None or battle_map is None:
        return []
    lines = []
    label = entity_label(door_obj)
    try:
        pos = battle_map.entity_or_object_pos(door_obj)
    except Exception:
        pos = None
    if pos is not None:
        lines.append(f"Door {label} at {pos}.")
    try:
        if callable(getattr(door_obj, 'opened', None)):
            lines.append(f"Door open: {bool(door_obj.opened())}")
    except Exception:
        pass
    try:
        if callable(getattr(door_obj, 'locked', None)):
            lines.append(f"Door locked: {bool(door_obj.locked())}")
    except Exception:
        pass
    try:
        actor_pos = battle_map.position_of(actor)
        if hasattr(door_obj, 'passable') and actor_pos is not None:
            lines.append(f"Door passable from your tile: {bool(door_obj.passable(actor_pos))}")
    except Exception:
        pass
    try:
        interactions = door_obj.available_interactions(actor, battle=None) or {}
        enabled = [
            name for name, details in interactions.items()
            if not (details or {}).get('disabled')
        ]
        if enabled:
            lines.append(f"Door interactions available: {', '.join(enabled)}")
    except Exception:
        pass
    return lines


def completed_offers_for_requester(
    session,
    actor: Entity,
    requester: Entity,
    *,
    game_properties=None,
) -> List[str]:
    if actor is None or requester is None or session is None:
        return []
    props = game_properties or getattr(session, 'game_properties', None) or {}
    configs = merged_offer_configs(props)
    completed = []
    for item_slug in configs:
        if has_completed_item_offer(session, actor, requester, item_slug):
            completed.append(item_slug)
    for item_slug in ('tavern_suite_key', 'tavern_room_key_1', 'tavern_room_key_2', 'tavern_room_key_3', 'tavern_room_key_4'):
        if item_slug in completed:
            continue
        if has_completed_item_offer(session, actor, requester, item_slug):
            completed.append(item_slug)
    return completed


def in_party_sidekick_prompt_block(session, entity, speaker=None) -> str:
    """Authoritative current-state text when ``entity`` is already a party sidekick.

    YAML backstory often still says “if invited, join with [JOIN_PARTY]”. This
    block overrides that so the NPC stays consistent after they have joined.
    """
    if entity is None or session is None:
        return ''
    try:
        from natural20.sidekick import (
            is_in_party_sidekick,
            iter_party_members,
            sidekick_owners,
        )
    except Exception:
        return ''
    try:
        if not is_in_party_sidekick(session, entity):
            return ''
    except Exception:
        return ''

    self_uid = str(getattr(entity, 'entity_uid', '') or '')
    companion_names = []
    party_uids = set()
    try:
        for member in iter_party_members(session) or []:
            uid = str(getattr(member, 'entity_uid', '') or '')
            if uid:
                party_uids.add(uid)
            if uid and uid == self_uid:
                continue
            name = entity_label(member)
            if name and name not in companion_names:
                companion_names.append(name)
    except Exception:
        pass

    lines = [
        'Current party status (authoritative; overrides any backstory about still waiting to be invited):',
        'You have already joined this adventuring party as a companion. You travel with them. This is settled.',
    ]
    if companion_names:
        lines.append(f'You travel with: {", ".join(companion_names)}.')
    owners = []
    try:
        owners = sidekick_owners(session, entity) or []
    except Exception:
        owners = []
    if owners and not companion_names:
        lines.append('You are a companion of this adventuring party.')

    if speaker is not None:
        speaker_uid = str(getattr(speaker, 'entity_uid', '') or '')
        speaker_name = entity_label(speaker)
        if speaker_uid and speaker_uid in party_uids:
            lines.append(
                f'The speaker ({speaker_name}) is a member of your party. '
                'Treat them as a traveling companion, not a stranger asking you to come along.'
            )

    lines.append(
        'Do not speak as if you are still deciding whether to go, still posted at your old duties, '
        'or not yet committed to this journey.'
    )
    return '\n'.join(lines) + '\n'


def build_npc_situational_context(
    session,
    current_game,
    actor: Entity,
    *,
    task: Optional[Dict[str, Any]] = None,
    requester: Entity = None,
    movement_helper=None,
) -> str:
    if actor is None or current_game is None:
        return ''

    lines: List[str] = ['Situational awareness (authoritative):']

    party_status = in_party_sidekick_prompt_block(session, actor, speaker=requester)
    if party_status:
        lines.append(party_status.rstrip())

    try:
        goal = current_game.get_short_term_goal(actor)
    except Exception:
        goal = None
    if isinstance(goal, dict) and goal.get('status') == 'active':
        lines.append(f"Active short-term goal: {goal.get('goal', '')}")
        if goal.get('arrival_debrief_done'):
            lines.append('Goal arrival debrief already triggered for this goal.')

    battle_map = None
    try:
        battle_map = current_game.get_map_for_entity(actor)
    except Exception:
        battle_map = None

    position = None
    if battle_map is not None:
        try:
            position = battle_map.entity_or_object_pos(actor)
        except Exception:
            pass
    map_name = getattr(battle_map, 'name', None) if battle_map is not None else None
    if position is not None and battle_map is not None:
        lines.append(f"Your position: {position} on map {map_name or 'unknown'}.")
        try:
            location_anns = current_location_annotations_at_position(
                battle_map.properties,
                int(position[0]),
                int(position[1]),
            )
            # Use full hierarchy to show enclosing area nesting
            location_line = format_location_hierarchy_for_llm(
                location_anns,
                map_name=map_name,
                position=(int(position[0]), int(position[1])),
            )
            if location_line:
                lines.append(location_line)
        except Exception:
            pass
    elif map_name:
        lines.append(f"Your map: {map_name}.")

    if task:
        stuck_ticks = int(task.get('stuck_ticks') or 0)
        if stuck_ticks > 0:
            lines.append(
                f"Movement stuck counter: {stuck_ticks} ticks without progress — "
                "re-evaluate whether your errand is already done."
            )
        last_pos = task.get('last_position')
        if position is not None and last_pos is not None and tuple(last_pos) == tuple(position):
            lines.append('You have not changed tiles recently.')

        route_blocker = task.get('route_blocker')
        if isinstance(route_blocker, dict):
            lines.append(
                f"Route blocked by: {route_blocker.get('label') or 'door'} "
                f"({route_blocker.get('state') or 'unknown state'})."
            )
            door_obj = route_blocker.get('object')
            lines.extend(_door_state_lines(actor, door_obj, battle_map))

        door_attempts = task.get('door_interact_attempts') or {}
        if door_attempts:
            parts = [f"{uid}:{count}" for uid, count in door_attempts.items() if count]
            if parts:
                lines.append(
                    f"Recent door interaction attempts: {', '.join(parts)}. "
                    "Do not repeat unlock/open if the guest already has the key or the door is passable."
                )

        target_info = task.get('target_info') or {}
        if movement_helper is not None:
            try:
                reached = movement_helper.movement_reached(
                    actor,
                    target_info,
                    distance_ft=task.get('distance_ft', 5),
                )
                lines.append(f"Movement destination reached: {reached}")
                lines.append(f"Movement target: {movement_helper.describe_target(target_info)}")
            except Exception:
                pass

    if requester is None and task:
        requester_uid = task.get('requester_uid')
        if requester_uid:
            try:
                requester = session.entity_by_uid(requester_uid)
            except Exception:
                requester = None

    if requester is not None:
        handle = mention_handle_for(requester)
        lines.append(f"Requester / guest: {entity_label(requester)} (@{handle}).")
        if battle_map is not None:
            try:
                distance_ft = int(battle_map.distance(actor, requester) * battle_map.feet_per_grid)
                lines.append(f"Requester distance: {distance_ft}ft.")
            except Exception:
                pass
        completed = completed_offers_for_requester(session, actor, requester)
        if completed:
            readable = ', '.join(slug.replace('_', ' ') for slug in completed)
            lines.append(f"Items you already offered this guest (accepted): {readable}.")
        if isinstance(requester, PlayerCharacter):
            for slug in ('tavern_suite_key', 'tavern_room_key_1', 'tavern_room_key_2', 'tavern_room_key_3', 'tavern_room_key_4'):
                if _inventory_qty(requester, slug) > 0:
                    lines.append(
                        f"Guest carries {slug.replace('_', ' ')} in inventory — "
                        "they can unlock the room themselves."
                    )

    key_slugs = ('tavern_skeleton_key', 'tavern_suite_key', 'tavern_room_key_1', 'tavern_room_key_2', 'tavern_room_key_3', 'tavern_room_key_4')
    if _inventory_qty(actor, 'tavern_skeleton_key') > 0:
        lines.append(
            'You carry the tavern skeleton key on your ring — it opens every Prancing Flagon lock '
            '(guest rooms, suite, and till safe).'
        )
    carried_keys = [slug for slug in key_slugs if slug != 'tavern_skeleton_key' and _inventory_qty(actor, slug) > 0]
    if carried_keys:
        lines.append(
            'Individual room keys still on your person: '
            + ', '.join(slug.replace('_', ' ') for slug in carried_keys)
            + '.'
        )
    elif _inventory_qty(actor, 'tavern_skeleton_key') <= 0:
        lines.append('You are not carrying any tavern room keys on your person (they may already be with the guest).')

    lines.append(
        "If you have led the guest to the correct door and given them the key, "
        "speak briefly to the guest, emit [GOAL_COMPLETE], and stop interacting with the door. "
        "Then include [MOVE: target=taproom, distance=5] to return behind the bar."
    )
    return '\n'.join(lines)
