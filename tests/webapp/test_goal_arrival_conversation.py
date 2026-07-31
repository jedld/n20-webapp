"""Tests for NPC goal-arrival debrief and situational movement context."""

import unittest
from unittest.mock import Mock, patch

from natural20.player_character import PlayerCharacter
from natural20.session import Session

from webapp.entity_rag_handler import EntityRAGHandler
from webapp.goal_arrival_conversation import GoalArrivalConversationManager
from webapp.npc_situational_context import build_npc_situational_context


class TestGoalArrivalConversationManager(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.session_state = {}
        self.session.game_time = 42

        self.current_game = Mock()
        self.current_game.game_session = self.session
        self.current_game.get_map_for_entity.return_value = Mock(name='tavern_suite')

        self.conversation_service = Mock()
        self.conversation_service._sids_for_username.return_value = []
        self.manager = GoalArrivalConversationManager(
            self.current_game,
            conversation_service_getter=lambda: self.conversation_service,
        )

        self.npc = Mock()
        self.npc.entity_uid = 'mara-1'
        self.npc.label.return_value = 'Mara'

        self.pc = Mock(spec=PlayerCharacter)
        self.pc.entity_uid = 'pc-1'

        self.current_game.get_entity_by_uid.return_value = self.pc
        self.current_game.entity_owners.return_value = ['player1']

    @patch.object(GoalArrivalConversationManager, '_emit_to_participants')
    def test_start_opens_goal_arrival_dialog(self, _emit):
        goal_state = {'goal': 'Escort guest to suite', 'status': 'active'}
        started = self.manager.start(self.npc, self.pc, goal_state=goal_state, task={'requester_uid': 'pc-1'})
        self.assertTrue(started)
        self.assertTrue(self.session.session_state['goal_arrival_conversation']['active'])
        self.conversation_service.initiate_npc_address.assert_called_once()
        kwargs = self.conversation_service.initiate_npc_address.call_args.kwargs
        self.assertTrue(kwargs.get('goal_arrival'))
        self.assertFalse(kwargs.get('forced'))
        self.assertTrue(goal_state.get('arrival_debrief_done'))

    @patch.object(GoalArrivalConversationManager, '_emit_to_participants')
    def test_on_player_no_response_ends_session(self, _emit):
        self.manager.start(self.npc, self.pc, goal_state={'goal': 'test', 'status': 'active'})
        self.assertTrue(self.manager.on_player_no_response(self.pc))
        self.assertIsNone(self.manager.active_state())

    @patch.object(GoalArrivalConversationManager, '_emit_to_participants')
    def test_maybe_complete_after_npc_reply_on_goal_complete(self, _emit):
        self.manager.start(self.npc, self.pc, goal_state={'goal': 'test', 'status': 'active'})
        completed = self.manager.maybe_complete_after_npc_reply(self.npc, 'All set. [GOAL_COMPLETE]')
        self.assertTrue(completed)
        self.assertIsNone(self.manager.active_state())


class TestNpcSituationalContext(unittest.TestCase):
    def setUp(self):
        self.session = Session(root_path='user_levels/wild_sheep_chase')
        self.current_game = Mock()
        self.current_game.get_short_term_goal.return_value = {
            'goal': 'Bring guest to suite',
            'status': 'active',
        }
        self.battle_map = Mock()
        self.battle_map.name = 'tavern_suite'
        self.battle_map.entity_or_object_pos.return_value = (5, 8)
        self.battle_map.position_of.return_value = (5, 8)
        self.battle_map.feet_per_grid = 5
        self.battle_map.distance.return_value = 1
        self.current_game.get_map_for_entity.return_value = self.battle_map

        self.npc = Mock()
        self.npc.entity_uid = 'mara-1'
        self.npc.inventory = {}

        self.pc = Mock(spec=PlayerCharacter)
        self.pc.entity_uid = 'pc-1'
        self.pc.inventory = {'tavern_suite_key': {'qty': 1}}

    def test_completed_offer_appears_in_context(self):
        self.session.load_state = Mock(return_value={
            'completed': {'mara-1:pc-1:tavern_suite_key': 100},
        })
        text = build_npc_situational_context(
            self.session,
            self.current_game,
            self.npc,
            task={'requester_uid': 'pc-1', 'stuck_ticks': 2},
            requester=self.pc,
        )
        self.assertIn('already offered', text.lower())
        self.assertIn('tavern suite key', text.lower())
        self.assertIn('guest carries', text.lower())
        self.assertIn('[GOAL_COMPLETE]', text)


class TestMovementArrivalSatisfied(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.current_game = Mock()
        self.rag = EntityRAGHandler(self.session, self.current_game)
        self.rag.movement_helper = Mock()
        self.rag.movement_helper.movement_reached.return_value = False

        self.actor = Mock()
        self.actor.entity_uid = 'npc-1'
        self.battle_map = Mock()
        self.battle_map.feet_per_grid = 5
        self.battle_map.distance.return_value = 1
        self.current_game.get_map_for_entity.return_value = self.battle_map

        self.door = Mock()
        self.door.entity_uid = 'door-1'
        self.route_blocker = {'entity_uid': 'door-1', 'object': self.door}
        self.target_info = {'kind': 'annotation', 'annotation': {'id': 'suite', 'pos': [5, 7]}}
        self.task = {'distance_ft': 5, 'door_interact_attempts': {'door-1': 3}}

    def test_adjacent_to_route_blocker_counts_as_arrival(self):
        satisfied = self.rag._movement_arrival_satisfied(
            self.actor,
            self.target_info,
            self.task,
            self.route_blocker,
        )
        self.assertTrue(satisfied)

    def test_max_door_attempts_adjacent_counts_as_arrival(self):
        self.route_blocker = {'entity_uid': 'door-1'}
        with patch.object(self.rag, '_hydrate_route_blocker', return_value={'object': self.door}):
            with patch.object(self.rag, '_adjacent_to_route_blocker', return_value=True):
                satisfied = self.rag._movement_arrival_satisfied(
                    self.actor,
                    self.target_info,
                    self.task,
                    self.route_blocker,
                )
        self.assertTrue(satisfied)


if __name__ == '__main__':
    unittest.main()
