"""Staff NPC retrieve/offer from workplace containers on another stack floor."""

import unittest
from unittest.mock import Mock, patch

from webapp.entity_rag_handler import EntityRAGHandler
from webapp.npc_item_exchange import (
    STAFF_CROSS_MAP_RETRIEVE,
    actor_is_container_staff,
    container_access_block_reason,
    maps_share_stack,
    retrieve_from_container,
)


class TestStaffContainerExchange(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.current_game = Mock()
        self.rag = EntityRAGHandler(self.session, self.current_game)
        self.rag.movement_helper = Mock()
        self.rag.movement_helper.movement_reached.return_value = False

        self.downstairs = Mock(name='town_market')
        self.downstairs.name = 'town_market'
        self.upstairs = Mock(name='tavern_2nd_floor')
        self.upstairs.name = 'tavern_2nd_floor'
        stack = Mock()
        stack.floors = [
            Mock(map_name='town_market', map=self.downstairs),
            Mock(map_name='tavern_2nd_floor', map=self.upstairs),
        ]
        self.downstairs.map_stack = stack
        self.upstairs.map_stack = stack

        self.pip = Mock()
        self.pip.entity_uid = 'pip_barmaid'
        self.pip.inventory = {}
        self.pip.label.return_value = 'Pip (Barmaid)'

        self.guest = Mock()
        self.guest.entity_uid = 'aldric'

        self.bar = Mock()
        self.bar.entity_uid = 'tavern_bar'
        self.bar.properties = {
            'staff_viewers': ['pip_barmaid', 'mara_bartender'],
            'staff_workplace': 'taproom',
        }
        self.bar.locked.return_value = False
        self.bar.opened.return_value = True
        self.bar.inventory = {'bread_loaf': {'qty': 3}}
        self.bar.transfer = Mock()

        self.current_game.get_map_for_entity.side_effect = lambda ent: {
            self.pip: self.upstairs,
            self.bar: self.downstairs,
        }.get(ent, self.downstairs)
        self.current_game.get_npc_movement_task.return_value = None
        self.current_game.schedule_npc_move_to.return_value = {
            'entity_uid': 'pip_barmaid',
            'status': 'active',
        }

    def test_staff_cross_map_block_reason(self):
        reason = container_access_block_reason(self.pip, self.bar, self.current_game)
        self.assertEqual(reason, STAFF_CROSS_MAP_RETRIEVE)
        self.assertTrue(actor_is_container_staff(self.pip, self.bar))
        self.assertTrue(maps_share_stack(self.upstairs, self.downstairs))

    def test_retrieve_queues_workplace_run_for_staff(self):
        plan = {
            'retrieve': {'item': 'bread_loaf', 'container': self.bar, 'qty': 1},
            'offer_item': {'item': 'bread_loaf', 'target': self.guest},
        }
        result = {'executed_actions': []}
        taproom_target = {'kind': 'annotation', 'annotation': {'id': 'taproom'}}
        with patch.object(self.rag, 'parse_move_target_info', return_value=taproom_target):
            with patch.object(self.rag, '_trigger_immediate_npc_movement'):
                self.rag._apply_item_exchange_directives(
                    plan,
                    self.pip,
                    speaker=self.guest,
                    result=result,
                    kick_movement=True,
                )
        self.current_game.schedule_npc_move_to.assert_called_once()
        scheduled = self.current_game.schedule_npc_move_to.return_value
        self.assertEqual(
            self.current_game.schedule_npc_move_to.call_args[0][1],
            taproom_target,
        )
        self.assertIn('pending_item_exchange', scheduled)
        pending = scheduled['pending_item_exchange']
        self.assertEqual(pending[0]['kind'], 'retrieve')
        self.assertEqual(pending[0]['container_uid'], 'tavern_bar')
        self.assertEqual(pending[1]['kind'], 'offer_item')
        self.assertEqual(pending[1]['target_uid'], 'aldric')
        self.assertIn('staff_workplace_exchange', result['executed_actions'])

    def test_pending_exchange_runs_when_staff_reaches_bar(self):
        task = {
            'requester_uid': 'aldric',
            'pending_item_exchange': [
                {'kind': 'retrieve', 'item': 'bread_loaf', 'container_uid': 'tavern_bar', 'qty': 1},
            ],
        }
        self.session.entity_by_uid.return_value = self.bar
        self.current_game.get_map_for_entity.side_effect = lambda ent: self.downstairs
        self.downstairs.distance.return_value = 1
        self.downstairs.feet_per_grid = 5
        self.downstairs.can_see.return_value = True

        with patch('webapp.npc_item_exchange._transfer_container_payload'):
            applied = self.rag._apply_pending_item_exchange(self.pip, task, self.guest)

        self.assertTrue(applied)
        self.assertNotIn('pending_item_exchange', task)


if __name__ == '__main__':
    unittest.main()
