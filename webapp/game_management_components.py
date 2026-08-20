import sys
import time

from natural20.generic_controller import GenericController
from natural20.llm_controller import LlmMcpController
from natural20.player_character import PlayerCharacter
from natural20.web.web_controller import WebController


class GameEntityRegistry:
    def __init__(self, manager):
        self.manager = manager

    def _assigned_materialization_state(self):
        """Return mutable state used to dampen repeated materialization noise."""
        state = getattr(self.manager, '_assigned_materialization_state', None)
        if state is None:
            state = {}
            self.manager._assigned_materialization_state = state
        return state

    def _campaign_default_map_key(self):
        session = getattr(self.manager, 'game_session', None)
        getter = getattr(session, 'default_map_key', None)
        if callable(getter):
            key = getter(getattr(self.manager, 'map_location', None))
            maps = self.manager.maps or {}
            if key in maps:
                return key
        maps = self.manager.maps
        if 'index' in maps:
            return 'index'
        if maps:
            return next(iter(maps))
        return None

    def _default_map_for_user(self, username):
        if username and username in self.manager.current_map_for_user:
            return self.manager.current_map_for_user[username]
        default_key = self._campaign_default_map_key()
        if default_key is None:
            raise KeyError('No maps loaded for campaign')
        return default_key, self.manager.maps[default_key]

    def defer_all_players(self):
        deferred_ids = set()
        session = getattr(self.manager, 'game_session', None)
        active = getattr(session, 'active_map_set', None)
        for entity_map in self.manager.maps.values():
            if active and session is not None:
                try:
                    if session.map_set_for(entity_map.name) != active:
                        continue
                except Exception:
                    pass
            for entity in list(entity_map.entities.keys()):
                if not isinstance(entity, PlayerCharacter):
                    continue

                entity_uid = str(getattr(entity, 'entity_uid', '') or '')
                if not entity_uid or entity_uid in deferred_ids:
                    continue

                position = list(entity_map.position_of(entity))
                entity_map.remove(entity)
                self.manager.deferred_players[entity_uid] = {
                    'entity': entity,
                    'map_name': entity_map.name,
                    'position': position,
                }
                deferred_ids.add(entity_uid)
                self.manager.logger.info(f"Deferred spawn for {entity_uid} at {position} on map {entity_map.name}")

    def defer_entities_on_map(self, map_name: str):
        """Remove player characters from a map and stash them for re-placement."""
        target_map = self.manager.maps.get(map_name)
        if target_map is None:
            return

        for entity in list(target_map.entities.keys()):
            if not isinstance(entity, PlayerCharacter):
                continue

            entity_uid = str(getattr(entity, 'entity_uid', '') or '')
            if not entity_uid:
                continue

            position = list(target_map.position_of(entity))
            target_map.remove(entity)
            self.manager.deferred_players[entity_uid] = {
                'entity': entity,
                'map_name': map_name,
                'position': position,
            }
            self.manager.logger.info(
                f"Deferred {entity_uid} at {position} on map {map_name} before reload"
            )

    def _resolve_respawn_location(self, username, entity_uid):
        from webapp.blueprints.helpers.runtime_state import get_index_data

        index_data = get_index_data() or {}
        map_name = None
        position = None

        if username in self.manager.current_map_for_user:
            map_name, _current_map = self.manager.current_map_for_user[username]

        if map_name is None:
            map_name = index_data.get('player_spawn_map') or self._campaign_default_map_key()

        target_map = self.manager.maps.get(map_name)
        if target_map is None:
            map_name = self._campaign_default_map_key()
            target_map = self.manager.maps.get(map_name)

        if target_map is not None and getattr(target_map, 'player_spawn_points', None):
            selectable = None
            for entry in index_data.get('selectable_characters') or []:
                overrides = entry.get('overrides') or {}
                if str(entry.get('name')) == entity_uid or str(overrides.get('entity_uid')) == entity_uid:
                    selectable = entry
                    break
            group = selectable.get('group') if selectable else None
            slot = target_map.allocate_player_spawn_point(entity_uid, group=group)
            if slot is not None:
                return map_name, list(slot['position'])

        pov = self.manager.pov_entity_for_user.get(username)
        if pov is not None:
            pov_map = self.get_map_for_entity(pov)
            if pov_map is not None:
                map_name = pov_map.name
                try:
                    position = list(pov_map.position_of(pov))
                except Exception:
                    position = None

        if position is None:
            position = [1, 1]
        return map_name, position

    def respawn_deferred_on_map(self, map_name: str):
        """Place any deferred player characters that belong on ``map_name``."""
        from webapp.blueprints.helpers.pvp import spawn_deferred_entity

        for entity_uid in list(self.manager.deferred_players.keys()):
            deferred = self.manager.deferred_players.get(entity_uid)
            if not deferred or deferred.get('map_name') != map_name:
                continue
            try:
                spawn_deferred_entity(entity_uid)
            except Exception:
                self.manager.logger.exception(
                    f"Failed to respawn deferred entity {entity_uid} on map {map_name}"
                )

    def ensure_user_entities_spawned(self, username):
        """Materialize and place characters assigned to ``username`` if missing from maps."""
        from webapp.blueprints.helpers.pvp import ensure_character_entity_loaded, spawn_deferred_entity

        spawned = list(self.spawn_player_for_user(username))
        materialize_state = self._assigned_materialization_state()
        retry_cooldown_s = float(getattr(self.manager, '_assigned_materialization_retry_cooldown_s', 1.0))

        for controller in self.manager.controllers:
            if username not in controller.get('controllers', []):
                continue

            entity_uid = str(controller['entity_uid'])
            state_key = (str(username), entity_uid)
            entity = self.get_entity_by_uid(entity_uid)
            if entity is not None and self.get_map_for_entity(entity) is not None:
                materialize_state.pop(state_key, None)
                continue

            if entity is None:
                now = time.time()
                state = materialize_state.setdefault(
                    state_key,
                    {'last_attempt_ts': 0.0, 'warned': False, '_t_start': 0.0},
                )
                if state['last_attempt_ts'] and now - state['last_attempt_ts'] < retry_cooldown_s:
                    continue
                state['last_attempt_ts'] = now
                state['_t_start'] = time.perf_counter()
                entity = ensure_character_entity_loaded(entity_uid)
            if entity is None:
                _t_materialize = time.perf_counter()
                state = materialize_state.setdefault(
                    state_key,
                    {'last_attempt_ts': 0.0, 'warned': False, '_t_start': 0.0},
                )
                if not state.get('warned'):
                    elapsed_ms = (_t_materialize - state.get('_t_start', _t_materialize)) * 1000
                    self.manager.logger.warning(
                        f"[timing] materialize_failed entity={entity_uid} user={username} "
                        f"lookup_ms={elapsed_ms:.1f}"
                    )
                    state['warned'] = True
                continue

            materialize_state.pop(state_key, None)

            if entity_uid not in self.manager.deferred_players:
                map_name, position = self._resolve_respawn_location(username, entity_uid)
                self.manager.deferred_players[entity_uid] = {
                    'entity': entity,
                    'map_name': map_name,
                    'position': position,
                }
                self.manager.logger.info(
                    f"Re-deferred {entity_uid} for user {username} at {position} on {map_name}"
                )

            try:
                placed = spawn_deferred_entity(entity_uid)
            except Exception:
                self.manager.logger.exception(
                    f"Failed to respawn assigned character {entity_uid} for user {username}"
                )
                continue

            if placed is not None and placed not in spawned:
                spawned.append(placed)

        return spawned

    def spawn_player_for_user(self, username):
        spawned = []
        for controller in self.manager.controllers:
            if username not in controller['controllers']:
                continue
            entity_uid = str(controller['entity_uid'])
            deferred = self.manager.deferred_players.get(entity_uid)
            if deferred is None:
                continue
            entity = deferred['entity']
            map_name = deferred['map_name']
            position = deferred['position']
            target_map = self.manager.maps.get(map_name)
            if target_map is None:
                continue
            pos_x, pos_y = position
            if not target_map.placeable(entity, pos_x, pos_y):
                pos_x, pos_y = target_map.find_empty_placeable_position(entity, pos_x, pos_y)
                self.manager.logger.info(f"Original position {position} occupied, using {pos_x},{pos_y} for {entity_uid}")
            target_map.place((pos_x, pos_y), entity)
            del self.manager.deferred_players[entity_uid]
            spawned.append(entity)
            self.manager.logger.info(f"Spawned {entity_uid} at ({pos_x},{pos_y}) on map {map_name} for user {username}")
        return spawned

    def get_pov_entity_for_user(self, username):
        # Reduced logging verbosity - info-level logging on every lookup was causing I/O overhead
        # self.manager.logger.debug(f"Getting POV entity for {username}")
        return self.manager.pov_entity_for_user.get(username, None)

    def set_pov_entity_for_user(self, username, entity):
        if entity:
            self.manager.logger.info(f"Setting POV entity for {username} to {entity.name}")
        self.manager.pov_entity_for_user[username] = entity

    def switch_map_for_user(self, username, map_name):
        self.manager.logger.info(f"Switching map for {username} to {map_name}")
        self.manager.current_map_for_user[username] = (map_name, self.manager.maps[map_name])

    def _iter_maps_in_active_set(self):
        session = getattr(self.manager, 'game_session', None)
        items = list((self.manager.maps or {}).items())
        if session is None:
            return items
        active = getattr(session, 'active_map_set', None)
        if not active:
            return items
        return [(name, map_obj) for name, map_obj in items if session.map_set_for(name) == active]

    def get_map_for_user(self, username):
        _name, battle_map = self._default_map_for_user(username)
        return battle_map

    def get_map_for_entity(self, entity):
        for _name, map_obj in self._iter_maps_in_active_set():
            if isinstance(entity, str):
                mapped_entity = map_obj.entity_by_uid(entity)
            else:
                mapped_entity = entity

            if mapped_entity in map_obj.entities:
                return map_obj
        return None

    def get_entity_by_uid(self, entity_uid):
        uid = str(entity_uid)
        # Prefer the live token object stored on the active map set over a stale
        # registry copy so heals, inventory changes, and battle commits mutate
        # what the UI is rendering.
        search_order = list(self._iter_maps_in_active_set())
        seen = {id(m) for _, m in search_order}
        for name, map_obj in (self.manager.maps or {}).items():
            if id(map_obj) not in seen:
                search_order.append((name, map_obj))
        for _name, map_obj in search_order:
            for map_entity in map_obj.entities.keys():
                if str(getattr(map_entity, 'entity_uid', '')) == uid:
                    return map_entity
        for _name, map_obj in search_order:
            entity = map_obj.entity_by_uid(entity_uid)
            if entity:
                return entity
        deferred = self.manager.deferred_players.get(uid)
        if deferred:
            return deferred['entity']
        return None

    def get_background_image_for_user(self, username, time_of_day_state=None):
        name, battle_map = self._default_map_for_user(username)
        if time_of_day_state is not None:
            try:
                filename = battle_map.background_image_for_time_of_day(time_of_day_state)
                if filename:
                    return 'maps/' + filename
            except Exception:
                pass
        if battle_map.background_image():
            return 'maps/' + battle_map.background_image()
        return 'maps/' + name + '.png'


class GameControllerRegistry:
    def __init__(self, manager):
        self.manager = manager

    def setup_controllers(self):
        for controller in self.manager.controllers:
            entity_uid = controller['entity_uid']
            self.manager.logger.info(f"Setting up controller for {entity_uid}")
            entity = self.manager.get_entity_by_uid(entity_uid)
            if entity is None:
                continue
            if entity not in self.manager.web_controllers:
                self.manager.web_controllers[entity] = WebController(self.manager.game_session, None)
                self.manager.web_controllers[entity].add_user('dm')

            for username in controller['controllers']:
                self.manager.web_controllers[entity].add_user(username)
        try:
            from webapp.blueprints.helpers.sidekick_control import restore_sidekick_controllers
            restore_sidekick_controllers(self.manager)
        except Exception:
            pass

    def get_controller_for_entity(self, entity):
        if entity is None:
            return None

        direct = self.manager.web_controllers.get(entity)
        if direct is not None:
            return direct

        entity_uid = getattr(entity, 'entity_uid', None)
        if not entity_uid:
            return None

        for key, controller in list(self.manager.web_controllers.items()):
            if getattr(key, 'entity_uid', None) != entity_uid:
                continue
            if key is not entity:
                self.manager.web_controllers.pop(key, None)
                self.manager.web_controllers[entity] = controller
            return controller
        return None

    def ensure_web_controller_for_entity(self, entity):
        """Return the manual WebController for a PC or in-party sidekick."""
        controller = self.get_controller_for_entity(entity)
        if controller is not None:
            return controller
        is_sidekick = False
        try:
            from natural20.sidekick import is_in_party_sidekick
            session = self.manager.game_session
            is_sidekick = is_in_party_sidekick(session, entity)
        except Exception:
            is_sidekick = False
        if not isinstance(entity, PlayerCharacter) and not is_sidekick:
            return None

        controller = WebController(self.manager.game_session, None)
        owners = self.entity_owners(entity)
        if not owners and is_sidekick:
            try:
                from natural20.sidekick import sidekick_owners
                owners = sidekick_owners(self.manager.game_session, entity)
            except Exception:
                owners = []
        if owners:
            for username in owners:
                controller.add_user(username)
        else:
            controller.add_user('dm')
        self.manager.web_controllers[entity] = controller
        return controller

    def effective_npc_combat_controller(self):
        if self.manager.force_llm_npc_combat:
            return 'llm'
        return self.manager.npc_controller

    def _build_controller_by_kind(self, kind):
        kind = (kind or '').lower()
        if kind == 'manual':
            controller = WebController(self.manager.game_session, None)
            controller.add_user('dm')
            return controller
        if kind == 'ai':
            return GenericController(self.manager.game_session)
        if kind == 'llm':
            try:
                from webapp.app import llm_handler as current_llm_handler  # type: ignore
                provider = getattr(current_llm_handler, 'current_provider', None)
            except Exception:
                provider = None
            controller_class = LlmMcpController
            utils_module = sys.modules.get('webapp.utils')
            if utils_module is not None:
                controller_class = getattr(utils_module, 'LlmMcpController', controller_class)
            return controller_class(self.manager.game_session, llm_provider=provider)
        return None

    def build_combat_controller_for_entity(self, entity, controller_kind=None):
        explicit = self._build_controller_by_kind(controller_kind)
        if explicit is not None:
            return explicit

        if isinstance(entity, PlayerCharacter):
            return self.ensure_web_controller_for_entity(entity)
        try:
            from natural20.sidekick import is_in_party_sidekick, sidekick_owners
            if is_in_party_sidekick(self.manager.game_session, entity) and sidekick_owners(
                self.manager.game_session, entity
            ):
                return self.ensure_web_controller_for_entity(entity)
        except Exception:
            pass
        if entity.familiar():
            return self.ensure_web_controller_for_entity(entity.owner) or self.get_controller_for_entity(entity.owner)

        npc_controller = self.effective_npc_combat_controller()

        if npc_controller == 'manual':
            web_controllers = WebController(self.manager.game_session, None)
            web_controllers.add_user('dm')
            return web_controllers

        if npc_controller == 'llm':
            built = self._build_controller_by_kind('llm')
            if built is not None:
                return built

        return GenericController(self.manager.game_session)

    def get_web_controllers_for_user(self, username, default_controller=None):
        controller_list = []
        for _entity, controller in self.manager.web_controllers.items():
            if username in controller.get_users():
                controller_list.append(controller)
        return controller_list

    def entity_owners(self, entity):
        owner = getattr(entity, 'owner', None)
        if owner is not None and getattr(owner, 'entity_uid', None) is not None:
            entity_uid = owner.entity_uid
        else:
            entity_uid = entity.entity_uid

        ctrl_info = next((controller for controller in self.manager.controllers if controller['entity_uid'] == entity_uid), None)
        return [] if not ctrl_info else ctrl_info['controllers']

    def entities_owned_by(self, entity):
        entities = []
        for _name, map_obj in self.manager.maps.items():
            for candidate in map_obj.entities:
                if candidate.owner == entity:
                    entities.append(candidate)
        return entities


class ShortTermGoalManager:
    def __init__(self, manager):
        self.manager = manager

    def schedule_short_term_goal(self, entity, goal_text, speaker=None):
        if entity is None:
            return None

        goal_text = (goal_text or '').strip()
        if not goal_text:
            return None

        goal_id = str(entity.entity_uid)
        requester_uid = getattr(speaker, 'entity_uid', None) if speaker is not None else None
        goal_record = {
            'entity_uid': goal_id,
            'goal': goal_text,
            'status': 'active',
            'created_at_game_time': self.manager.game_session.game_time,
            'updated_at_game_time': self.manager.game_session.game_time,
            'next_run_at': time.time() + self.manager.goal_turn_seconds,
            'attempts': 0,
            'history': [],
            'requester_uid': requester_uid,
            'last_error': None,
            'running': False,
        }
        with self.manager.game_state_lock:
            self.manager.short_term_goals[goal_id] = goal_record
        return goal_record

    def get_short_term_goal(self, entity):
        if entity is None:
            return None
        return self.manager.short_term_goals.get(str(entity.entity_uid))

    def record_short_term_goal_history(self, entity, entry):
        if entity is None:
            return None
        goal = self.manager.short_term_goals.get(str(entity.entity_uid))
        if goal is None:
            return None
        goal['history'].append(entry)
        goal['history'] = goal['history'][-12:]
        goal['updated_at_game_time'] = self.manager.game_session.game_time
        return goal

    def complete_short_term_goal(self, entity, status='completed', reason=None):
        if entity is None:
            return None
        goal = self.manager.short_term_goals.get(str(entity.entity_uid))
        if goal is None:
            return None
        goal['status'] = status
        goal['running'] = False
        goal['next_run_at'] = None
        goal['last_error'] = reason
        goal['updated_at_game_time'] = self.manager.game_session.game_time
        if reason:
            goal['history'].append({
                'time': self.manager.game_session.game_time,
                'event': status,
                'reason': reason,
            })
            goal['history'] = goal['history'][-12:]
        return goal

    def goal_worker(self):
        while not self.manager._goal_thread_stop.is_set():
            if self.manager.get_current_battle() or not self.manager.short_term_goals:
                self.manager._goal_thread_stop.wait(self.manager.goal_poll_interval)
                continue
            if getattr(self.manager, 'realtime_ui_suppressed', lambda: False)():
                self.manager._goal_thread_stop.wait(self.manager.goal_poll_interval)
                continue

            due_goal_ids = []
            now = time.time()
            with self.manager.game_state_lock:
                for goal_id, goal in self.manager.short_term_goals.items():
                    if goal.get('status') != 'active':
                        continue
                    if goal.get('running'):
                        continue
                    next_run_at = goal.get('next_run_at')
                    if next_run_at is None or next_run_at > now:
                        continue
                    goal['running'] = True
                    due_goal_ids.append(goal_id)

            for goal_id in due_goal_ids:
                try:
                    from webapp.blueprints.helpers.runtime_state import (
                        get_entity_rag_handler,
                        get_llm_conversation_handler,
                    )
                    rag_handler = get_entity_rag_handler()
                    llm_handler = get_llm_conversation_handler()
                    if rag_handler is not None and llm_handler is not None:
                        rag_handler.execute_scheduled_goal(goal_id, llm_handler)
                except Exception as exc:
                    self.manager.logger.error(f"Failed to execute short-term goal for {goal_id}: {exc}")
                    entity = self.manager.get_entity_by_uid(goal_id)
                    if entity is not None:
                        self.record_short_term_goal_history(entity, {
                            'time': self.manager.game_session.game_time,
                            'event': 'error',
                            'reason': str(exc),
                        })
                finally:
                    with self.manager.game_state_lock:
                        goal = self.manager.short_term_goals.get(goal_id)
                        if goal is not None:
                            goal['running'] = False
                            if goal.get('status') == 'active':
                                goal['attempts'] = goal.get('attempts', 0) + 1
                                goal['next_run_at'] = time.time() + self.manager.goal_turn_seconds
                                goal['updated_at_game_time'] = self.manager.game_session.game_time

            self.manager._goal_thread_stop.wait(self.manager.goal_poll_interval)