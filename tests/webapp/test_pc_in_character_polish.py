"""Tests for player in-character conversation polish."""

import unittest
from unittest.mock import MagicMock

from natural20.player_character import PlayerCharacter
from webapp.conversation_service import ConversationService


class _FakePC(PlayerCharacter):
    def __init__(self, session, props):
        super().__init__(session, props)

    def label(self):
        return self.properties.get('name', 'Hero')

    def class_descriptor(self):
        return 'fighter 3'

    def race(self):
        return 'human'


class TestPcInCharacterPolish(unittest.TestCase):
    def _service(self, *, llm_handler=None):
        game = MagicMock()
        service = ConversationService(
            current_game_getter=lambda: game,
            game_session=MagicMock(),
            socketio=MagicMock(),
            entity_rag_handler_getter=lambda: MagicMock(),
            llm_conversation_handler_getter=lambda: llm_handler,
            roles_for_username_getter=lambda: (lambda username: []),
            entity_owners_getter=lambda: (lambda username: []),
            entities_controlled_by_getter=lambda: (lambda username: []),
            logins_getter=lambda: {},
            logger=MagicMock(),
        )
        service._test_game = game
        return service

    def test_requester_can_control_entity_via_pov(self):
        service = self._service()
        entity = MagicMock(entity_uid='pc-1')
        service._test_game.get_pov_entity_for_user.return_value = entity
        self.assertTrue(service._requester_can_control_entity('alice', entity))

    def test_polish_pc_in_character_line_uses_npc_llm(self):
        controller = MagicMock()
        controller._effective_send_message.return_value = 'By my honor, I shall stand watch.'
        service = self._service(llm_handler=controller)

        session = MagicMock()
        pc = _FakePC(session, {
            'name': 'Aldric',
            'race': 'human',
            'background': 'soldier',
            'personality_traits': 'I face problems head-on.',
            'classes': {'fighter': 3},
            'level': 3,
            'ability': {'str': 16, 'dex': 12, 'con': 14, 'int': 10, 'wis': 11, 'cha': 9},
            'max_hp': 24,
        })

        polished = service.polish_pc_in_character_line(pc, 'yeah I will guard the door', language='common')
        self.assertEqual(polished, 'By my honor, I shall stand watch.')
        controller._effective_send_message.assert_called_once()
        messages = controller._effective_send_message.call_args[0][0]
        self.assertIn('soldier', messages[0]['content'].lower())
        self.assertIn('personality', messages[0]['content'].lower())

    def test_talk_response_polish_only_returns_preview_payload(self):
        service = self._service()
        entity = MagicMock(spec=PlayerCharacter)
        entity.entity_uid = 'pc-1'
        service._test_game.get_entity_by_uid.return_value = entity
        service._requester_can_control_entity = MagicMock(return_value=True)
        service.polish_pc_in_character_line = MagicMock(return_value='Hail, friend.')

        payload, status = service.talk_response(
            {
                'entity_id': 'pc-1',
                'message': 'hello there',
                'in_character_polish': True,
                'polish_only': True,
            },
            'system prompt',
            requesting_username='alice',
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get('success'))
        self.assertEqual(payload.get('draft_message'), 'hello there')
        self.assertEqual(payload.get('polished_message'), 'Hail, friend.')


if __name__ == '__main__':
    unittest.main()
