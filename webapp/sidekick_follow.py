"""Sidekick auto-follow: keep in-party sidekicks trailing the focused character.

Each in-party sidekick with ``auto_follow`` enabled gets a persistent
``mode='follow'`` task in the ``NpcMovementManager``.  The line is chained:
the first auto-follow sidekick targets the owning player's POV (focused)
entity, and every subsequent auto-follow sidekick targets the previous one,
so several sidekicks trail in a line.  ``distance_ft=5`` (one square) keeps
the follower adjacent to -- but never stacked on -- the target.

Movement itself is handled by the existing follow pipeline: committed moves
cascade through ``NpcMovementManager.on_entity_moved`` and the movement
worker ticks as a fallback, with pathfinding (and door navigation) provided
by ``NpcMovementHelper.build_move_step``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from natural20.map_set import iter_player_characters
from natural20.player_character import PlayerCharacter
from natural20.sidekick import (
    is_auto_follow_sidekick,
    iter_in_party_sidekicks,
    sidekick_owners,
)
from natural20.utils.conversation import mention_handle_for

#: One square of separation from the followed entity (5 ft on a standard map).
AUTO_FOLLOW_DISTANCE_FT = 5
#: Marker stored on movement tasks owned by this module.
SIDEKICK_FOLLOW_TAG = 'sidekick_follow'


def _uid(entity) -> str:
    return str(getattr(entity, 'entity_uid', '') or '')


def _resolve_leader_entity(current_game, leader_sidekick):
    """The 'focused' character the first sidekick should follow.

    Prefers the POV entity of one of the sidekick's owners; falls back to the
    first player character in the party.
    """
    session = getattr(current_game, 'game_session', None)
    if session is None:
        return None
    for owner in sidekick_owners(session, leader_sidekick):
        try:
            pov = current_game.get_pov_entity_for_user(owner)
        except Exception:
            pov = None
        if pov is not None:
            return pov
    try:
        for entity in iter_player_characters(session):
            if isinstance(entity, PlayerCharacter):
                return entity
    except Exception:
        pass
    return None


def _ensure_follow_task(manager, sidekick, target) -> bool:
    """Create or update the follow task for ``sidekick``. True if changed."""
    target_uid = _uid(target)
    task = manager.get_task(sidekick)
    if task is not None:
        if task.get('mode') == 'follow' and task.get('status') == 'active':
            if task.get('target_uid') == target_uid:
                task.setdefault(SIDEKICK_FOLLOW_TAG, True)
                return False
            task['target_uid'] = target_uid
            task['target_info'] = {
                'kind': 'entity',
                'target': target,
                'target_spec': mention_handle_for(target),
            }
            task.setdefault(SIDEKICK_FOLLOW_TAG, True)
            task['stuck_ticks'] = 0
            manager.kick(sidekick)
            return True
        if (
            task.get('mode') != 'follow'
            and task.get('status') == 'active'
            and not task.get(SIDEKICK_FOLLOW_TAG)
        ):
            # Another live movement task (e.g. an errand) owns this entity;
            # do not steal it mid-errand.  It re-syncs when it completes.
            return False

    task = manager.schedule_follow(
        sidekick,
        target,
        distance_ft=AUTO_FOLLOW_DISTANCE_FT,
        speaker=target,
    )
    if task is None:
        return False
    task[SIDEKICK_FOLLOW_TAG] = True
    return True


def _cancel_follow_task(manager, sidekick, reason: str) -> bool:
    task = manager.get_task(sidekick)
    if task is None or task.get('mode') != 'follow':
        return False
    if not task.get(SIDEKICK_FOLLOW_TAG) and task.get('status') == 'active':
        # Not ours (e.g. an LLM conversation told the NPC to follow someone);
        # leave it alone.
        return False
    manager.cancel(sidekick, reason=reason, mode='follow')
    return True


def sync_sidekick_auto_follows(current_game) -> list[str]:
    """Align follow tasks with the sidekicks' ``auto_follow`` flags.

    Returns the entity UIDs whose task was created, updated, or cancelled so
    callers can decide whether a UI refresh is needed.
    """
    session = getattr(current_game, 'game_session', None)
    manager = getattr(current_game, 'movement_manager', None)
    if session is None or manager is None:
        return []

    line = [
        entity
        for entity in iter_in_party_sidekicks(session)
        if is_auto_follow_sidekick(session, entity)
    ]
    leader = _resolve_leader_entity(current_game, line[0]) if line else None

    changed = []
    active_uids = set()
    for index, sidekick in enumerate(line):
        target = leader if index == 0 else line[index - 1]
        if target is None:
            if _cancel_follow_task(manager, sidekick, 'auto-follow leader unavailable'):
                changed.append(_uid(sidekick))
            continue
        active_uids.add(_uid(sidekick))
        try:
            if _ensure_follow_task(manager, sidekick, target):
                changed.append(_uid(sidekick))
        except Exception:
            continue
        # Auto-follow tasks are persistent: the movement worker counts every
        # tick against ``attempts`` and abandons tasks at a fixed limit, so
        # keep the counters clear while the sidekick remains in the line.
        task = manager.get_task(sidekick)
        if task is not None and task.get('mode') == 'follow' and task.get('status') == 'active':
            task['attempts'] = 0
            task['stuck_ticks'] = 0

    # Cancel leftover auto-follow tasks (sidekick left the party, flag off,
    # or no longer in the line).
    for task_id in list(getattr(manager, 'npc_movements', {}) or {}):
        if task_id in active_uids:
            continue
        task = manager.npc_movements.get(task_id)
        if not task or not task.get(SIDEKICK_FOLLOW_TAG):
            continue
        entity = None
        try:
            entity = manager.get_entity_by_uid(task_id)
        except Exception:
            entity = None
        if entity is not None and _cancel_follow_task(manager, entity, 'sidekick auto-follow disabled'):
            changed.append(task_id)
    return changed
