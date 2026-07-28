"""Integration tests for NPC approach/movement near doors (wild_sheep_chase / town_market)."""

import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

from natural20.action import Action
from natural20.map import Map
from natural20.player_character import PlayerCharacter
from natural20.session import Session
from webapp.entity_rag_handler import EntityRAGHandler


class TestApproachThroughDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.join(os.path.dirname(__file__), '..', '..', 'user_levels', 'wild_sheep_chase')
        if not os.path.isdir(root):
            raise unittest.SkipTest('wild_sheep_chase campaign not available')
        cls.session = Session(root_path=root)

    def setUp(self):
        self.test_map = Map(self.session, 'maps/town_market.yml')
        self.npc = self.test_map.entity_by_uid('pip_barmaid')
        self.aldric = PlayerCharacter.load(self.session, 'characters/aldric_fighter.yml')
        self.current_game = Mock()
        self.current_game.get_current_battle.return_value = None
        self.current_game.entity_owners.return_value = ['dm']
        self.current_game.get_entity_by_uid = lambda uid: {
            'pip_barmaid': self.npc,
            'aldric': self.aldric,
        }.get(uid)
        self.current_game.get_map_for_entity = Mock(return_value=self.test_map)
        self.current_game.commit_and_update = Mock(side_effect=self._fake_commit_and_update)
        self.current_game.schedule_npc_move_to = Mock(return_value={'mode': 'move_to', 'status': 'active'})
        self.current_game.emit_out_of_combat_move_animation = Mock(return_value=True)
        self.rag = EntityRAGHandler(self.session, self.current_game)

    def _fake_commit_and_update(self, username, action, pov_entities):
        if getattr(action, 'action_type', None) == 'move':
            path = getattr(action, 'move_path', None) or []
            if len(path) >= 2:
                dest = path[-1]
                if isinstance(dest, (tuple, list)) and len(dest) >= 2:
                    self.test_map.move_to(action.source, int(dest[0]), int(dest[1]))
        else:
            action.resolve(self.session, self.test_map)
            for item in list(getattr(action, 'result', []) or []):
                for klass in Action.__subclasses__():
                    other_results = klass.apply(None, item, session=self.session)
                    if isinstance(other_results, list):
                        action.result.extend(other_results)
        return True

    def test_approach_directive_steps_toward_player_past_closed_door(self):
        self.test_map.place((12, 17), self.npc)
        self.test_map.place((14, 16), self.aldric)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()

        target_info = {'kind': 'entity', 'target': self.aldric}
        plan = {
            'approach': {
                'target_info': target_info,
                'distance_ft': 5,
            },
        }

        with patch.object(self.rag, '_execution_username_for', return_value='dm'):
            result = self.rag.apply_response_plan_directives(
                plan,
                self.npc,
                speaker=self.aldric,
                only={'approach'},
            )

        self.assertIn('approach', result.get('executed_actions') or [])
        self.current_game.commit_and_update.assert_called()
        self.assertNotEqual(self.test_map.position_of(self.npc), (12, 17))

    def test_approach_schedules_followup_without_sync_movement_tick(self):
        self.test_map.place((12, 17), self.npc)
        self.test_map.place((14, 16), self.aldric)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()

        target_info = {'kind': 'entity', 'target': self.aldric}
        plan = {
            'approach': {
                'target_info': target_info,
                'distance_ft': 2,
            },
        }

        with patch.object(self.rag, '_execution_username_for', return_value='dm'):
            with patch.object(self.rag, 'execute_scheduled_movement') as execute_tick:
                self.rag.apply_response_plan_directives(
                    plan,
                    self.npc,
                    speaker=self.aldric,
                    only={'approach'},
                )
                execute_tick.assert_not_called()

        self.current_game.schedule_npc_move_to.assert_called_once()

    def test_deferred_movement_kick_runs_on_background_thread(self):
        barrier = threading.Barrier(2, timeout=3)
        seen = {}

        def fake_execute(entity_uid, handler, **kwargs):
            seen['thread'] = threading.current_thread().name
            seen['entity_uid'] = entity_uid
            barrier.wait()

        self.rag.execute_scheduled_movement = fake_execute
        self.rag._trigger_immediate_npc_movement(self.npc)

        barrier.wait()
        self.assertEqual(seen['entity_uid'], 'pip_barmaid')
        self.assertNotEqual(seen['thread'], threading.current_thread().name)

    def test_movement_tick_can_auto_open_blocking_door(self):
        self.test_map.place((12, 17), self.npc)
        self.test_map.place((14, 16), self.aldric)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': {'kind': 'entity', 'target': self.aldric},
            'distance_ft': 5,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }
        route_blocker = self.rag.movement_helper.detect_blocking_door_on_route(
            self.npc,
            task['target_info'],
        )
        self.assertIsNotNone(route_blocker)

        door_obj = route_blocker['object']
        self.current_game.get_npc_movement_task = Mock(return_value=task)
        self.current_game.get_map_for_entity = Mock(return_value=self.test_map)
        self.rag.movement_helper.current_game = self.current_game

        cleared = self.rag.try_clear_route_blocking_door(
            self.npc,
            route_blocker,
            task['target_info'],
        )
        self.assertTrue(cleared)
        self.current_game.commit_and_update.assert_called()
        self.assertTrue(any(obj.opened() for obj in self.test_map.objects_at(13, 17)))

    def test_approach_then_movement_ticks_do_not_recurse_or_crash(self):
        self.test_map.place((12, 17), self.npc)
        self.test_map.place((14, 16), self.aldric)
        for obj in self.test_map.objects_at(13, 17):
            obj.close()

        target_info = {'kind': 'entity', 'target': self.aldric}
        plan = {
            'approach': {
                'target_info': target_info,
                'distance_ft': 5,
            },
        }

        task = {
            'status': 'active',
            'mode': 'move_to',
            'target_info': target_info,
            'distance_ft': 5,
            'next_run_at': time.time() - 1,
            'requester_uid': None,
        }
        self.current_game.get_npc_movement_task = Mock(return_value=task)
        self.current_game.complete_npc_movement = Mock()
        self.rag.movement_helper.current_game = self.current_game

        start_pos = self.test_map.position_of(self.npc)
        with patch.object(self.rag, '_execution_username_for', return_value='dm'):
            self.rag.apply_response_plan_directives(
                plan,
                self.npc,
                speaker=self.aldric,
                only={'approach'},
            )

            tick_calls = 0
            for _ in range(12):
                tick_calls += 1
                self.rag.execute_scheduled_movement(
                    'pip_barmaid',
                    llm_conversation_handler=None,
                    allow_movement_llm=False,
                    advance_time=False,
                )
                if self.rag.movement_helper.movement_reached(
                    self.npc,
                    target_info,
                    distance_ft=5,
                ):
                    break

        self.assertGreater(tick_calls, 0)
        self.assertLessEqual(tick_calls, 12)
        self.assertNotEqual(self.test_map.position_of(self.npc), start_pos)
        self.assertTrue(
            self.rag.movement_helper.movement_reached(self.npc, target_info, distance_ft=5)
            or any(obj.opened() for obj in self.test_map.objects_at(13, 17))
        )


if __name__ == '__main__':
    unittest.main()
