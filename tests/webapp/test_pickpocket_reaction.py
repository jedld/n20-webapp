import unittest
from unittest.mock import MagicMock, Mock, patch

from webapp.pickpocket_reaction import (
    _resolve_npc_entity,
    process_pickpocket_reactions,
)


class TestPickpocketReaction(unittest.TestCase):
    def test_resolve_npc_entity_prefers_npc_entity_field(self):
        npc = MagicMock(entity_uid='witness_npc')
        session = MagicMock()
        resolved = _resolve_npc_entity(session, {'npc_entity': npc, 'npc': {'uid': 'other'}})
        self.assertIs(resolved, npc)

    def test_failed_pickpocket_schedules_victim_and_witness_replies(self):
        witness = MagicMock()
        witness.entity_uid = 'witness_npc'
        witness.label.return_value = 'Bystander'
        witness.is_npc.return_value = True
        witness.dead.return_value = False
        witness.conscious.return_value = True
        witness.npc.return_value = True

        victim = MagicMock()
        victim.entity_uid = 'victim_npc'
        victim.label.return_value = 'Merchant'
        victim.is_npc.return_value = True
        victim.dead.return_value = False
        victim.conscious.return_value = True
        victim.npc.return_value = True

        pc = MagicMock()
        pc.entity_uid = 'aldric'
        pc.label.return_value = 'Aldric'
        pc.is_npc.return_value = False

        action = MagicMock()
        action.__class__.__name__ = 'PickpocketAction'
        action.source = pc
        action.result = [{
            'type': 'pickpocket',
            'target': victim,
            'success': False,
            'item_name': 'gold coin',
        }]

        game = MagicMock()
        game.game_session = MagicMock()
        game.get_current_battle.return_value = None
        game.get_map_for_entity.return_value = MagicMock()
        game.username_to_sid = {'player1': ['sid-1']}
        game.socketio = MagicMock()

        detection = {
            'noticed': True,
            'npc_entity': witness,
            'npc': {'name': 'Bystander', 'uid': 'witness_npc'},
            'detection_type': 'seen',
            'can_see_pickpocketer': True,
        }

        conversation_service = MagicMock()
        conversation_service.llm_conversation_handler = MagicMock()

        scheduled_jobs = []

        def _run_now(job):
            scheduled_jobs.append(job)
            job()

        game.socketio.start_background_task.side_effect = _run_now

        with patch(
            'webapp.pickpocket_reaction.evaluate_pickpocket_detection',
            return_value=[detection],
        ), patch(
            'webapp.blueprints.helpers.runtime_state.get_conversation_service',
            return_value=conversation_service,
        ):
            results = process_pickpocket_reactions(
                game,
                action,
                in_combat=False,
                username='player1',
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(game.socketio.start_background_task.call_count, 2)
        self.assertEqual(conversation_service.handle_npc_pickpocket_detected.call_count, 2)

        victim_call = conversation_service.handle_npc_pickpocket_detected.call_args_list[0].kwargs
        witness_call = conversation_service.handle_npc_pickpocket_detected.call_args_list[1].kwargs
        self.assertEqual(victim_call['role'], 'victim')
        self.assertEqual(witness_call['role'], 'witness')
        self.assertEqual(victim_call['requesting_username'], 'player1')
        game.socketio.emit.assert_called_once()


def test_handle_npc_pickpocket_detected_runs_reply_turn():
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

    victim = _StubNpc('merchant_npc', 'Merchant')
    thief = _StubPc('aldric', 'Aldric')
    service, _llm_handler = _build_service()
    service._run_single_npc_reply_turn = Mock(return_value={'message': 'Thief!'})

    payload, status = service.handle_npc_pickpocket_detected(
        victim,
        thief,
        target=victim,
        item_name='gold coin',
        success=False,
        role='victim',
        conversation_system_prompt='{backstory}',
    )

    assert status == 200
    assert payload['success'] is True
    service._run_single_npc_reply_turn.assert_called_once()
    inject_args = service._run_single_npc_reply_turn.call_args
    assert inject_args[0][0] is victim
    assert inject_args[0][1] is thief
    assert 'PICKPOCKET CAUGHT' in inject_args[1]['inject_system_messages'][0]
    assert inject_args[1]['npc_initiated'] is True
    assert inject_args[1]['local_chat_only'] is True


if __name__ == '__main__':
    unittest.main()
