"""Tests for NPC movement helper and movement directives."""

import time
import unittest
from unittest.mock import Mock, patch

from natural20.map import Map
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from webapp.entity_rag_handler import EntityRAGHandler
from webapp.npc_movement import NpcMovementHelper, NpcMovementManager


class TestNpcMovementHelper(unittest.TestCase):
    def setUp(self):
        self.game_session = Mock()
        self.game_session.game_time = 100
        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = None
        self.helper = NpcMovementHelper(self.game_session, self.current_game)

    def test_parse_square_spec(self):
        self.assertEqual(self.helper.parse_square_spec('3, 5'), (3, 5))
        self.assertEqual(self.helper.parse_square_spec('3 5'), (3, 5))
        self.assertIsNone(self.helper.parse_square_spec('invalid'))

    def test_movement_reached_for_square(self):
        actor = Mock()
        battle_map = Mock()
        battle_map.position_of.return_value = (3, 5)
        self.current_game.get_map_for_entity.return_value = battle_map

        reached = self.helper.movement_reached(
            actor,
            {'kind': 'square', 'square': (3, 5)},
            distance_ft=0,
        )
        self.assertTrue(reached)

    def test_movement_target_viable_rejects_unreachable_cross_map_target(self):
        actor = Mock()
        target = Mock()
        target.entity_uid = 'sheep-1'

        map_a = Mock()
        map_a.name = 'town_market'
        map_b = Mock()
        map_b.name = 'wizard_tower'
        map_a.position_of.side_effect = lambda ent: (1, 1)
        map_b.position_of.side_effect = lambda ent: (4, 4)

        def map_for_entity(ent):
            if ent is actor:
                return map_a
            if ent is target:
                return map_b
            return None

        self.current_game.get_map_for_entity.side_effect = map_for_entity

        with patch('webapp.npc_movement.PathCompute') as mock_pc:
            mock_pc.return_value.compute_cross_map_path.return_value = None
            viable, reason = self.helper.movement_target_viable(
                actor,
                {'kind': 'entity', 'target': target, 'target_spec': '@Wizard sheep'},
            )
        self.assertFalse(viable)
        self.assertIn('wizard_tower', reason)


class TestNpcMovementDoorNavigation(unittest.TestCase):
    def setUp(self):
        self.session = Session(root_path='user_levels/wild_sheep_chase')
        self.game_session = self.session
        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = None
        self.helper = NpcMovementHelper(self.game_session, self.current_game)
        self.test_map = Map(self.session, 'maps/town_market.yml')
        self.npc = self.test_map.entity_by_uid('pip_barmaid')
        self.npc.properties['known_places'] = ['taproom']
        self.test_map.place((2, 6), self.npc)
        self.current_game.get_map_for_entity.return_value = self.test_map

    def test_should_use_door_navigation_for_known_landmark(self):
        target_info = {
            'kind': 'annotation',
            'annotation': {'id': 'taproom', 'label': 'Taproom', 'kind': 'point', 'pos': [2, 3]},
        }
        self.assertTrue(self.helper.should_use_door_navigation(self.npc, target_info))
        unknown = {
            'kind': 'annotation',
            'annotation': {'id': 'cellar', 'label': 'Cellar', 'kind': 'point', 'pos': [4, 4]},
        }
        self.assertFalse(self.helper.should_use_door_navigation(self.npc, unknown))
        entity_target = {'kind': 'entity', 'target': Mock()}
        self.assertTrue(self.helper.should_use_door_navigation(self.npc, entity_target))

    def test_detect_blocking_door_on_known_landmark_route(self):
        self.test_map.place((12, 17), self.npc)
        target_info = {
            'kind': 'annotation',
            'annotation': {'id': 'taproom', 'label': 'Taproom', 'kind': 'point', 'pos': [14, 16]},
        }
        for obj in self.test_map.objects_at(13, 17):
            obj.close()
        blocker = self.helper.detect_blocking_door_on_route(self.npc, target_info)
        self.assertIsNotNone(blocker)
        self.assertIn('label', blocker)
        self.assertEqual(blocker['from_position'], [12, 17])
        self.assertEqual(blocker['position'], [13, 17])

    def test_build_move_step_uses_door_navigation_for_entity_approach(self):
        aldric = PlayerCharacter.load(self.session, 'characters/aldric_fighter.yml')
        self.test_map.place((14, 16), aldric)
        self.test_map.place((12, 18), self.npc)
        for obj in self.test_map.objects_at(13, 17):
            obj.open()
        target_info = {'kind': 'entity', 'target': aldric}
        move_action = self.helper.build_move_step(self.npc, target_info, distance_ft=5)
        self.assertIsNotNone(move_action)
        self.assertGreaterEqual(len(move_action.move_path), 2)

    def test_detect_blocking_door_for_entity_approach(self):
        aldric = PlayerCharacter.load(self.session, 'characters/aldric_fighter.yml')
        self.test_map.place((14, 16), aldric)
        self.test_map.place((12, 17), self.npc)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()
        target_info = {'kind': 'entity', 'target': aldric}
        blocker = self.helper.detect_blocking_door_on_route(self.npc, target_info)
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker['position'], [13, 17])

    def test_resolve_reachable_square_for_door_landmark(self):
        self.test_map.place((12, 17), self.npc)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()
        resolved = self.helper._resolve_reachable_square(self.npc, self.test_map, (13, 17))
        self.assertIsNotNone(resolved)
        self.assertNotEqual(resolved, (13, 17))
        self.assertEqual(abs(resolved[0] - 13) + abs(resolved[1] - 17), 1)

    def test_build_move_step_cross_map_landmark_via_stairs(self):
        town = self.session.maps['town_market']
        upstairs = self.session.maps['tavern_2nd_floor']
        self.current_game.get_map_for_entity.return_value = town
        npc = town.entity_by_uid('pip_barmaid')
        npc.properties['known_places'] = ['landmark_standard_room_1']
        town.place((9, 14), npc)
        target_info = {
            'kind': 'annotation',
            'annotation': {
                'id': 'landmark_standard_room_1',
                'kind': 'point',
                'pos': [7, 1],
                'label': 'Standard Room 1',
            },
            'annotation_map': upstairs,
            'target_spec': 'landmark_standard_room_1',
        }
        viable, reason = self.helper.movement_target_viable(npc, target_info)
        self.assertTrue(viable, reason)
        move_action = self.helper.build_move_step(npc, target_info)
        self.assertIsNotNone(move_action)
        dest = tuple(move_action.move_path[-1])
        self.assertIn(dest, {(9, 15), (9, 16)})

    def test_movement_reached_false_until_on_landmark_map(self):
        town = self.session.maps['town_market']
        upstairs = self.session.maps['tavern_2nd_floor']
        self.current_game.get_map_for_entity.return_value = town
        npc = town.entity_by_uid('pip_barmaid')
        town.place((9, 14), npc)
        target_info = {
            'kind': 'annotation',
            'annotation': {
                'id': 'landmark_standard_room_1',
                'kind': 'point',
                'pos': [7, 1],
                'label': 'Standard Room 1',
            },
            'annotation_map': upstairs,
        }
        self.assertFalse(self.helper.movement_reached(npc, target_info, distance_ft=0))

    def test_movement_target_viable_allows_cross_map_entity_follow(self):
        town = self.session.maps['town_market']
        upstairs = self.session.maps['tavern_2nd_floor']
        npc = town.entity_by_uid('pip_barmaid')
        leader = town.entity_by_uid('henrik_patron')
        town.place((12, 18), npc)
        town.remove(leader)
        upstairs.place((1, 3), leader)

        def map_for_entity(ent):
            if ent is leader:
                return upstairs
            return town

        self.current_game.get_map_for_entity.side_effect = map_for_entity

        viable, reason = self.helper.movement_target_viable(
            npc,
            {'kind': 'entity', 'target': leader, 'target_spec': '@henrik'},
        )
        self.assertTrue(viable, reason)

    def test_build_move_step_toward_cross_map_follow_target(self):
        town = self.session.maps['town_market']
        upstairs = self.session.maps['tavern_2nd_floor']
        npc = town.entity_by_uid('pip_barmaid')
        leader = town.entity_by_uid('henrik_patron')
        town.place((12, 18), npc)
        town.remove(leader)
        upstairs.place((1, 3), leader)

        def map_for_entity(ent):
            if ent is leader:
                return upstairs
            return town

        self.current_game.get_map_for_entity.side_effect = map_for_entity

        move = self.helper.build_move_step(
            npc,
            {'kind': 'entity', 'target': leader, 'target_spec': '@henrik'},
            distance_ft=5,
        )
        self.assertIsNotNone(move)


class TestNpcMovementManager(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        self.manager.game_session = Mock()
        self.manager.game_session.game_time = 0
        self.manager.goal_turn_seconds = 6
        self.manager.goal_poll_interval = 1.0
        self.manager.npc_movements = {}
        from threading import RLock
        self.manager.game_state_lock = RLock()
        self.movement_manager = NpcMovementManager(self.manager)

    def test_schedule_move_to_and_cancel(self):
        entity = Mock()
        entity.entity_uid = 'npc-1'
        target_info = {'kind': 'square', 'square': (4, 4)}

        task = self.movement_manager.schedule_move_to(entity, target_info, distance_ft=0)
        self.assertEqual(task['mode'], 'move_to')
        self.assertEqual(self.movement_manager.get_task(entity)['target_info']['square'], (4, 4))

        cancelled = self.movement_manager.cancel(entity, reason='test', mode='move_to')
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_schedule_follow(self):
        entity = Mock()
        entity.entity_uid = 'npc-1'
        target = Mock()
        target.entity_uid = 'pc-1'

        task = self.movement_manager.schedule_follow(entity, target, distance_ft=5)
        self.assertEqual(task['mode'], 'follow')
        self.assertEqual(task['target_uid'], 'pc-1')

    def test_on_entity_moved_triggers_follower_tick(self):
        leader = Mock()
        leader.entity_uid = 'pc-1'
        follower = Mock()
        follower.entity_uid = 'npc-1'

        self.manager.get_current_battle.return_value = None
        self.manager.npc_movements = {
            'npc-1': {
                'status': 'active',
                'mode': 'follow',
                'target_uid': 'pc-1',
                'running': False,
            },
        }
        self.manager.get_entity_by_uid.return_value = follower

        with patch('webapp.blueprints.helpers.runtime_state.get_entity_rag_handler') as get_rag, patch(
            'webapp.blueprints.helpers.runtime_state.get_llm_conversation_handler'
        ) as get_llm:
            rag = Mock()
            get_rag.return_value = rag
            get_llm.return_value = Mock()
            self.movement_manager.on_entity_moved(leader)
            rag.execute_scheduled_movement.assert_called_once_with(
                'npc-1',
                get_llm.return_value,
                triggered_by_follow=True,
            )


class TestEntityRagMovementDirectives(unittest.TestCase):
    def setUp(self):
        self.mock_game_session = Mock()
        self.mock_game_session.game_time = 0
        self.mock_game_session.game_properties = {}
        self.mock_current_game = Mock()
        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []
        self.mock_current_game.output_logger = Mock()
        self.mock_current_game.output_logger.get_visible_entries_for_entity.return_value = []
        self.rag_handler = EntityRAGHandler(self.mock_game_session, self.mock_current_game)

    def test_parse_move_square_and_follow_directives(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        speaker = Mock()
        speaker.entity_uid = 'pc-1'

        follow_target = Mock()
        follow_target.entity_uid = 'pc-1'
        self.rag_handler.resolve_named_target = Mock(return_value=follow_target)

        directives = self.rag_handler.parse_action_directives(
            "[MOVE: x=4, y=7] [FOLLOW: target=speaker, distance=10] [STOP_MOVE]",
            actor,
            speaker=speaker,
        )

        self.assertEqual(directives['move_to']['target_info']['square'], (4, 7))
        self.assertEqual(directives['follow']['target'], follow_target)
        self.assertEqual(directives['follow']['distance_ft'], 10)
        self.assertTrue(directives['stop_move'])

    def test_apply_response_plan_schedules_move_and_follow(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        target = Mock()
        target.entity_uid = 'pc-1'

        self.mock_current_game.get_current_battle.return_value = None
        self.mock_current_game.entity_owners.return_value = []
        self.mock_current_game.schedule_npc_move_to.return_value = {'mode': 'move_to'}
        self.mock_current_game.schedule_npc_follow.return_value = {'mode': 'follow'}
        self.mock_current_game.cancel_npc_movement.return_value = {'status': 'cancelled'}

        move_plan = {
            'stop_move': False,
            'stop_follow': False,
            'move_to': {
                'target_info': {'kind': 'square', 'square': (2, 2)},
                'distance_ft': 0,
            },
        }
        move_result = self.rag_handler.apply_response_plan_directives(move_plan, actor)
        self.mock_current_game.schedule_npc_move_to.assert_called_once()
        self.assertIn('move_to', move_result['executed_actions'])

        follow_plan = {
            'stop_move': False,
            'stop_follow': False,
            'follow': {'target': target, 'distance_ft': 5},
        }
        follow_result = self.rag_handler.apply_response_plan_directives(follow_plan, actor)
        self.mock_current_game.schedule_npc_follow.assert_called_once()
        self.assertIn('follow', follow_result['executed_actions'])

        stop_plan = {
            'stop_move': True,
            'stop_follow': True,
        }
        stop_result = self.rag_handler.apply_response_plan_directives(stop_plan, actor)
        self.assertEqual(self.mock_current_game.cancel_npc_movement.call_count, 2)
        self.assertIn('stop_move', stop_result['executed_actions'])
        self.assertIn('stop_follow', stop_result['executed_actions'])

    def test_apply_approach_commits_and_emits_move_animation(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        target = Mock()
        move_action = Mock()
        move_action.action_type = 'move'
        move_action.move_path = [(1, 1), (2, 2)]

        self.mock_current_game.entity_owners.return_value = ['dm']
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=move_action)
        self.mock_current_game.commit_and_update.return_value = True
        self.mock_current_game.emit_out_of_combat_move_animation.return_value = True

        plan = {
            'approach': {
                'target_info': {'kind': 'entity', 'target': target},
                'distance_ft': 5,
            },
        }
        result = self.rag_handler.apply_response_plan_directives(plan, actor, only={'approach'})

        self.mock_current_game.commit_and_update.assert_called_once()
        self.mock_current_game.emit_out_of_combat_move_animation.assert_called_once_with(move_action, 'dm')
        self.assertIn('approach', result['executed_actions'])

    def test_maybe_promote_escort_approach_to_move_for_suite(self):
        from natural20.session import Session

        session = Session(root_path='user_levels/wild_sheep_chase')
        game = Mock()
        game.get_current_battle.return_value = None
        game.get_map_for_entity.side_effect = lambda ent: session.map_for_entity(ent)
        handler = EntityRAGHandler(session, game)

        pip = session.entity_by_uid('pip_barmaid')
        speaker = session.entity_by_uid('aldric') or Mock()
        if getattr(speaker, 'entity_uid', None) is None:
            speaker = Mock()
            speaker.entity_uid = 'aldric'

        plan = {
            'message': "Alright then, I'll be up in a jiffy.",
            'narrative': [],
            'approach': {
                'target_info': {'kind': 'entity', 'target': speaker},
                'distance_ft': 5,
            },
        }
        handler.movement_helper.movement_reached = Mock(return_value=True)
        handler.parse_move_target_info = Mock(
            return_value={
                'kind': 'annotation',
                'annotation': {'id': 'tavern_suite_room'},
            },
        )

        promoted = handler.maybe_promote_escort_approach_to_move(
            plan,
            pip,
            speaker=speaker,
            player_message='Meet me in the suite upstairs?',
        )

        self.assertTrue(promoted)
        self.assertIsNone(plan.get('approach'))
        self.assertEqual(plan['move_to']['target_info']['annotation']['id'], 'tavern_suite_room')

    def test_escort_navigation_inject_note_for_suite_request(self):
        from natural20.session import Session

        session = Session(root_path='user_levels/wild_sheep_chase')
        handler = EntityRAGHandler(session, Mock())
        pip = session.entity_by_uid('pip_barmaid')

        note = handler.escort_navigation_inject_note(
            pip,
            'Can you show me the tavern suite?',
        )

        self.assertIn('MOVE: target=tavern_suite_room', note)
        self.assertIn('APPROACH', note)

    def test_execute_scheduled_movement_completes_when_reached(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'square', 'square': (1, 1)},
            'distance_ft': 0,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=True)

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=None)
        self.mock_current_game.complete_npc_movement.assert_called_once()
        self.assertEqual(result['movement_status'], 'completed')

    def test_execute_scheduled_follow_stays_active_when_in_range(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True
        target = Mock()
        target.entity_uid = 'pc-1'

        task = {
            'status': 'active',
            'mode': 'follow',
            'target_info': {'kind': 'entity', 'target': target},
            'target_uid': 'pc-1',
            'distance_ft': 5,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.mock_game_session.entity_by_uid.return_value = target
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=True)
        self.rag_handler.movement_helper.movement_target_viable = Mock(return_value=(True, ''))
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=None)

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=None)

        self.mock_current_game.complete_npc_movement.assert_not_called()
        self.assertEqual(result['movement_status'], 'active')

    def test_execute_scheduled_movement_skips_llm_when_auto_step_succeeds(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        move_action = Mock()
        move_action.move_path = [[1, 1], [1, 2]]

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'entity', 'target': Mock()},
            'distance_ft': 5,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=None)
        self.rag_handler._commit_exploration_move = Mock(return_value=True)
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=move_action)

        llm_handler = Mock()
        llm_handler.generate_response.return_value = '[NO_RESPONSE]'

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=llm_handler)

        llm_handler.generate_response.assert_not_called()
        self.assertIn('move_to', result.get('executed_actions', []))

    def test_execute_scheduled_movement_forces_llm_when_door_blocks_route(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {
                'kind': 'annotation',
                'annotation': {'id': 'taproom', 'kind': 'point', 'pos': [2, 3]},
            },
            'distance_ft': 0,
            'next_run_at': time.time() + 60,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value={
            'label': 'Front Door',
            'position': [2, 5],
            'from_position': [2, 6],
            'state': 'closed, locked',
            'available_actions': ['unlock', 'open'],
            'mention': '@front-door',
            'entity_uid': 'door-1',
        })
        self.rag_handler.movement_helper.serialize_route_blocker = Mock(side_effect=lambda b: {
            k: v for k, v in b.items() if k != 'object'
        })
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=None)
        self.rag_handler.try_clear_route_blocking_door = Mock(return_value=False)
        llm_handler = Mock()
        llm_handler.generate_response.return_value = '[INTERACT: target=@front-door, action=unlock]'
        self.rag_handler.build_conversation_response_plan = Mock(return_value={'interact': None})
        self.rag_handler.apply_response_plan_directives = Mock(return_value={
            'executed_actions': [],
            'movement_status': 'active',
        })

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=llm_handler)

        llm_handler.generate_response.assert_called_once()
        self.rag_handler.movement_helper.build_move_step.assert_called()
        self.assertNotIn('move_to', result.get('executed_actions', []))

    def test_execute_scheduled_movement_auto_opens_blocking_door(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        door_obj = Mock()
        door_obj.entity_uid = 'door-1'
        interact_action = Mock()

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {
                'kind': 'annotation',
                'annotation': {'id': 'taproom', 'kind': 'point', 'pos': [14, 16]},
            },
            'distance_ft': 0,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        route_blocker = {
            'label': 'Front Door',
            'position': [13, 17],
            'from_position': [12, 17],
            'state': 'closed',
            'available_actions': ['open'],
            'mention': '@front-door',
            'entity_uid': 'door-1',
            'object': door_obj,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=route_blocker)
        self.rag_handler.movement_helper.serialize_route_blocker = Mock(side_effect=lambda b: {
            k: v for k, v in b.items() if k != 'object'
        })
        self.rag_handler.build_interact_action = Mock(return_value=interact_action)
        self.rag_handler._commit_exploration_move = Mock(return_value=True)
        move_action = Mock()
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=move_action)

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=None)

        self.rag_handler.build_interact_action.assert_called_once_with(actor, door_obj, 'open')
        self.mock_current_game.commit_and_update.assert_called_once()
        self.assertIn('interact', result.get('executed_actions', []))
        self.assertIn('move_to', result.get('executed_actions', []))

    def test_execute_scheduled_movement_does_not_kick_on_move_directive(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'entity', 'target': Mock()},
            'distance_ft': 2,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=None)
        self.rag_handler.movement_helper.movement_snapshot = Mock(return_value='snapshot')
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=None)

        llm_handler = Mock()
        llm_handler.conversations = {}
        llm_handler.generate_response.return_value = '[NO_RESPONSE]'
        captured = {}

        def capture_apply(plan, entity, speaker=None, **kwargs):
            captured.update(kwargs)
            return {'executed_actions': [], 'movement_status': 'active'}

        self.rag_handler.build_conversation_response_plan = Mock(return_value={})
        self.rag_handler.apply_response_plan_directives = capture_apply
        self.rag_handler._trigger_immediate_npc_movement = Mock()

        self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=llm_handler)

        llm_handler.generate_response.assert_called_once()
        self.assertFalse(captured.get('kick_movement', True))
        self.rag_handler._trigger_immediate_npc_movement.assert_not_called()

    def test_immediate_movement_tick_skips_movement_llm(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'entity', 'target': Mock()},
            'distance_ft': 2,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=None)
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=None)

        llm_handler = Mock()
        llm_handler.generate_response.return_value = '[NO_RESPONSE]'

        self.rag_handler._trigger_immediate_npc_movement(actor, llm_conversation_handler=llm_handler)

        time.sleep(0.2)

        llm_handler.generate_response.assert_not_called()
        self.mock_current_game.advance_world_time.assert_not_called()

    @patch('webapp.entity_rag_handler._NPC_MOVEMENT_STUCK_TICKS', 2)
    @patch('webapp.entity_rag_handler._NPC_MOVEMENT_LLM_STUCK_TICKS', 1)
    def test_execute_scheduled_movement_abandons_after_llm_round_without_progress(self):
        actor = Mock()
        actor.entity_uid = 'npc-1'
        actor.dialog = True

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'entity', 'target': Mock(), 'target_spec': '@Wizard sheep'},
            'distance_ft': 5,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
            'stuck_ticks': 0,
        }

        self.mock_current_game.get_entity_by_uid.return_value = actor
        self.mock_current_game.get_npc_movement_task.return_value = task
        self.mock_current_game.get_current_battle.return_value = None
        self.rag_handler.movement_helper.movement_reached = Mock(return_value=False)
        self.rag_handler.movement_helper.movement_target_viable = Mock(return_value=(True, ''))
        self.rag_handler.movement_helper.detect_blocking_door_on_route = Mock(return_value=None)
        self.rag_handler.movement_helper.movement_snapshot = Mock(return_value='snapshot')
        self.rag_handler.movement_helper.build_move_step = Mock(return_value=None)

        llm_handler = Mock()
        llm_handler.conversations = {}
        llm_handler.generate_response.return_value = '[NO_RESPONSE]'

        self.rag_handler.build_conversation_response_plan = Mock(return_value={})
        self.rag_handler.apply_response_plan_directives = Mock(
            return_value={'executed_actions': [], 'movement_status': 'active'},
        )

        result = self.rag_handler.execute_scheduled_movement('npc-1', llm_conversation_handler=llm_handler)

        self.mock_current_game.complete_npc_movement.assert_called_once()
        self.assertEqual(result.get('movement_status'), 'abandoned')
        llm_handler.generate_response.assert_called_once()


if __name__ == '__main__':
    unittest.main()
