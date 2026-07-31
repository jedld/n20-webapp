"""Tests for NPC escort / conversation-triggered movement scheduling."""

import unittest
from unittest.mock import MagicMock, Mock

from natural20.session import Session

from webapp.entity_rag_handler import EntityRAGHandler
from webapp.npc_movement import NpcMovementManager


class TestNpcEscortMovement(unittest.TestCase):
    def setUp(self):
        self.session = Session(root_path='user_levels/wild_sheep_chase')
        self.mara = self.session.maps['town_market'].entity_by_uid('mara_bartender')
        self.speaker = Mock()
        self.speaker.entity_uid = 'mira'

        self.game = MagicMock()
        self.game.get_current_battle.return_value = None
        self.game.get_map_for_entity.return_value = self.session.maps['town_market']
        self.game.entity_owners.return_value = ['dm']
        self.game.realtime_ui_suppressed.return_value = False
        self.game.schedule_npc_move_to = Mock(return_value={'status': 'active'})
        self.game.schedule_npc_follow = Mock(return_value=None)
        self.game.cancel_npc_movement = Mock()
        self.game.complete_short_term_goal = Mock()
        self.game.schedule_short_term_goal = Mock(return_value=None)

        self.rag = EntityRAGHandler(self.session, self.game)

    def test_move_to_scheduled_for_suite_landmark(self):
        response = '[MOVE: target=tavern_suite_room, distance=5] "Follow me."'
        plan = self.rag.build_conversation_response_plan(response, self.mara, self.speaker)
        self.assertIsNotNone((plan.get('move_to') or {}).get('target_info'))

        result = self.rag.apply_response_plan_directives(
            plan,
            self.mara,
            speaker=self.speaker,
            only={'move_to'},
            kick_movement=False,
        )
        self.game.schedule_npc_move_to.assert_called_once()
        self.assertIn('move_to', result['executed_actions'])

    def test_move_to_allowed_when_battle_active_but_npc_not_combatant(self):
        battle = MagicMock()
        battle.entities = []
        self.game.get_current_battle.return_value = battle

        response = '[MOVE: target=tavern_suite_room, distance=5]'
        plan = self.rag.build_conversation_response_plan(response, self.mara, self.speaker)
        self.rag.apply_response_plan_directives(
            plan,
            self.mara,
            speaker=self.speaker,
            only={'move_to'},
        )
        self.game.schedule_npc_move_to.assert_called_once()

    def test_move_to_blocked_when_npc_is_combatant(self):
        battle = MagicMock()
        battle.entities = [self.mara]
        self.game.get_current_battle.return_value = battle

        response = '[MOVE: target=tavern_suite_room, distance=5]'
        plan = self.rag.build_conversation_response_plan(response, self.mara, self.speaker)
        self.rag.apply_response_plan_directives(
            plan,
            self.mara,
            speaker=self.speaker,
            only={'move_to'},
        )
        self.game.schedule_npc_move_to.assert_not_called()

    def test_movement_manager_blocks_only_combatants(self):
        manager = MagicMock()
        battle = MagicMock()
        battle.entities = [self.mara]
        manager.get_current_battle.return_value = battle
        movement = NpcMovementManager(manager)

        self.assertTrue(movement.exploration_movement_blocked(self.mara))

        other = Mock()
        other.entity_uid = 'wolf-1'
        self.assertFalse(movement.exploration_movement_blocked(other))


if __name__ == '__main__':
    unittest.main()
