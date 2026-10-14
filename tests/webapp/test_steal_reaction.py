import unittest
from unittest.mock import MagicMock, Mock, patch

from webapp.steal_reaction import (
    _items_taken_from_container,
    _resolve_npc_entity,
    process_steal_reactions,
)


class TestStealReaction(unittest.TestCase):
    def test_items_taken_from_container_reads_from_direction(self):
        payload = {
            'from': {'items': ['bread_loaf', 'ale_mug'], 'qty': ['1', '0']},
            'to': {'items': [], 'qty': []},
        }
        taken = _items_taken_from_container(payload)
        self.assertEqual(taken, [{'item': 'bread_loaf', 'qty': 1}])

    def test_resolve_npc_entity_prefers_npc_entity_field(self):
        npc = MagicMock(entity_uid='mara_bartender')
        session = MagicMock()
        resolved = _resolve_npc_entity(session, {'npc_entity': npc, 'npc': {'uid': 'other'}})
        self.assertIs(resolved, npc)

    def test_process_steal_reactions_schedules_immediate_npc_reply(self):
        npc = MagicMock()
        npc.entity_uid = 'mara_bartender'
        npc.label.return_value = 'Mara'
        npc.is_npc.return_value = True

        pc = MagicMock()
        pc.entity_uid = 'aldric'
        pc.label.return_value = 'Aldric'
        pc.is_npc.return_value = False

        bar = MagicMock()
        bar.entity_uid = 'tavern_bar'
        bar.label.return_value = 'Bar Counter'

        action = MagicMock()
        action.__class__.__name__ = 'InteractAction'
        action.source = pc
        action.result = [{
            'type': 'interact',
            'target': bar,
            'items': {
                'from': {'items': ['bread_loaf'], 'qty': ['1']},
                'to': {'items': [], 'qty': []},
            },
        }]

        game = MagicMock()
        game.game_session = MagicMock()
        game.get_current_battle.return_value = None
        game.get_map_for_entity.return_value = MagicMock()
        game.username_to_sid = {'player1': ['sid-1']}
        game.socketio = MagicMock()

        detection = {
            'noticed': True,
            'npc_entity': npc,
            'npc': {'name': 'Mara', 'uid': 'mara_bartender'},
            'detection_type': 'seen',
            'can_see_pc': True,
            'ownership': {'owner': None, 'staff': ['mara_bartender'], 'location': None},
        }

        conversation_service = MagicMock()
        conversation_service.llm_conversation_handler = MagicMock()

        scheduled_jobs = []

        def _run_now(job):
            scheduled_jobs.append(job)
            job()

        game.socketio.start_background_task.side_effect = _run_now

        with patch(
            'webapp.steal_reaction.evaluate_steal_detection',
            return_value=[detection],
        ), patch(
            'webapp.blueprints.helpers.runtime_state.get_conversation_service',
            return_value=conversation_service,
        ):
            results = process_steal_reactions(
                game,
                action,
                in_combat=False,
                username='player1',
            )

        self.assertEqual(len(results), 1)
        game.socketio.start_background_task.assert_called_once()
        conversation_service.handle_npc_steal_detected.assert_called_once()
        kwargs = conversation_service.handle_npc_steal_detected.call_args.kwargs
        self.assertEqual(kwargs['detection_type'], 'seen')
        self.assertEqual(kwargs['requesting_username'], 'player1')
        game.socketio.emit.assert_called_once()


def test_handle_npc_steal_detected_runs_reply_turn():
    import importlib
    import os
    import sys

    webapp_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
    if webapp_dir not in sys.path:
        sys.path.insert(0, webapp_dir)
    importlib.import_module('webapp.conversation_service')
    # Load the sibling test module by path: the tests tree is not a package
    # (no __init__.py), so a dotted import is not portable across pytest
    # import modes / working directories.
    import importlib.util as _ilu
    _sibling = os.path.join(os.path.dirname(__file__), 'test_npc_damage_reaction.py')
    _spec = _ilu.spec_from_file_location('_npc_damage_reaction_shared', _sibling)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _StubNpc, _StubPc, _build_service = _mod._StubNpc, _mod._StubPc, _mod._build_service

    npc = _StubNpc('mara_bartender', 'Mara')
    thief = _StubPc('aldric', 'Aldric')
    service, _llm_handler = _build_service()
    service._run_single_npc_reply_turn = Mock(return_value={'message': 'Hey you!'})

    payload, status = service.handle_npc_steal_detected(
        npc,
        thief,
        container_info={'name': 'Bar Counter', 'uid': 'tavern_bar'},
        items=[{'item': 'bread_loaf', 'qty': 1}],
        detection_type='seen',
        conversation_system_prompt='{backstory}',
    )

    assert status == 200
    assert payload['success'] is True
    service._run_single_npc_reply_turn.assert_called_once()
    inject_args = service._run_single_npc_reply_turn.call_args
    assert inject_args[0][0] is npc
    assert inject_args[0][1] is thief
    assert 'THEFT DETECTED' in inject_args[1]['inject_system_messages'][0]
    assert inject_args[1]['npc_initiated'] is True
    assert inject_args[1]['local_chat_only'] is True


if __name__ == '__main__':
    unittest.main()
