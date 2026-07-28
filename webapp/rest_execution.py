"""Shared rest execution for HTTP routes, MCP tools, and the DM assistant."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RestPickController:
    """Inline controller used to drive the engine's rest hooks from automation."""

    def __init__(self, arcane_picks=None, hit_die_picks=None):
        self._arcane_picks = list(arcane_picks or [])
        self._hit_die_picks = list(hit_die_picks or [])
        self.consumed_picks: List[int] = []
        self.consumed_hit_die: List[int] = []

    def arcane_recovery_ui(self, entity, available_levels):
        while self._arcane_picks:
            level = self._arcane_picks.pop(0)
            if level in available_levels:
                self.consumed_picks.append(level)
                return level
        return None

    def prompt_hit_die_roll(self, entity, available_die_types):
        while self._hit_die_picks:
            die_type = self._hit_die_picks.pop(0)
            if die_type in available_die_types:
                self.consumed_hit_die.append(die_type)
                return die_type
        return 'skip'


def entity_rest_snapshot(entity) -> Dict[str, Any]:
    snapshot = {
        'hp': entity.hp(),
        'max_hp': entity.max_hp(),
        'hit_die': dict(entity.hit_die()) if hasattr(entity, 'hit_die') else {},
        'spell_slots': {
            klass: dict(slots) for klass, slots in (getattr(entity, 'spell_slots', {}) or {}).items()
        },
        'resources': {
            name: pool.to_dict() for name, pool in (getattr(entity, 'resources', None) or {}).items()
        },
        'statuses': list(entity.statuses) if hasattr(entity, 'statuses') else [],
    }
    for attr in (
        'arcane_recovery', 'second_wind_count', 'lay_on_hands_count', 'ki_count', 'max_ki',
        'bardic_inspiration_count', 'bardic_inspiration_max', 'wild_shape_count', 'wild_shape_max',
        'rage_count', 'rage_max', 'raging', 'rage_rounds_remaining', 'reckless_attack_active',
    ):
        if hasattr(entity, attr):
            snapshot[attr] = getattr(entity, attr)
    return snapshot


def execute_entity_rest(
    game,
    entity,
    rest_type: str,
    *,
    force: bool = False,
    arcane_picks=None,
    hit_die_picks=None,
    emit_refresh: bool = True,
) -> Dict[str, Any]:
    """Run a short or long rest for `entity` and return a JSON-friendly result."""
    rest_type = (rest_type or 'short').lower()
    if rest_type not in ('short', 'long'):
        return {'success': False, 'error': "type must be 'short' or 'long'"}

    battle = game.get_current_battle()
    battle_map = game.get_map_for_entity(entity)
    before = entity_rest_snapshot(entity)
    rest_controller = RestPickController(arcane_picks=arcane_picks, hit_die_picks=hit_die_picks)

    controller_holder = {}
    if battle is not None:
        original_controller_for = battle.controller_for

        def _proxy_controller_for(target, _orig=original_controller_for, _ent=entity, _ctrl=rest_controller):
            if target is _ent:
                return _ctrl
            return _orig(target)

        battle.controller_for = _proxy_controller_for
        controller_holder['restore'] = lambda: setattr(battle, 'controller_for', original_controller_for)

    npc_simulation_scheduled = False
    try:
        if rest_type == 'short':
            entity.short_rest(battle, force=force, prompt=bool(hit_die_picks), battle_map=battle_map)
            time_advance = 60 * 60
        else:
            entity.long_rest(battle=battle, battle_map=battle_map, force=force, require_rations=True)
            time_advance = 8 * 60 * 60

        try:
            old_time = int(getattr(game.game_session, 'game_time', 0) or 0)
            game.game_session.increment_game_time(time_advance)
            new_time = int(game.game_session.game_time)
            from webapp.blueprints.helpers.time_of_day import on_game_time_changed
            on_game_time_changed(game, old_time=old_time, new_time=new_time)
        except Exception:
            logger.exception('Failed to advance game time after rest')

        game_time_after_rest = game.game_session.game_time
        if rest_type == 'long':
            try:
                from webapp.long_rest_npc_simulation import schedule_long_rest_npc_simulation
                npc_simulation_scheduled = schedule_long_rest_npc_simulation(
                    game,
                    resting_entity=entity,
                    game_time=game_time_after_rest,
                )
            except Exception:
                logger.exception('Failed to schedule long rest NPC simulation')

        if emit_refresh and not npc_simulation_scheduled:
            try:
                game.socketio.emit('message', {'type': 'refresh_map'})
            except Exception:
                logger.exception('Failed to emit refresh_map after rest')

        return {
            'success': True,
            'entity_uid': getattr(entity, 'entity_uid', None),
            'entity_name': entity.label() if hasattr(entity, 'label') else str(entity),
            'type': rest_type,
            'forced': bool(force),
            'before': before,
            'after': entity_rest_snapshot(entity),
            'arcane_picks_consumed': rest_controller.consumed_picks,
            'hit_die_consumed': rest_controller.consumed_hit_die,
            'game_time': game_time_after_rest,
            'npc_simulation_scheduled': npc_simulation_scheduled,
        }
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'entity_uid': getattr(entity, 'entity_uid', None)}
    except Exception as exc:
        logger.exception('Failed to run rest for %s', getattr(entity, 'entity_uid', entity))
        return {'success': False, 'error': str(exc), 'entity_uid': getattr(entity, 'entity_uid', None)}
    finally:
        if 'restore' in controller_holder:
            controller_holder['restore']()
