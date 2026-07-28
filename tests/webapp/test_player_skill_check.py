"""Tests for JRPG dialog skill-check rolls via /talk."""

import importlib
import logging
import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

conversation_service = importlib.import_module('webapp.conversation_service')

from webapp.conversation_service import (
    ConversationService,
    is_npc_llm_failure_response,
    skill_check_reply_key,
)


class _StubRoll:
    def __init__(self, total):
        self._total = total

    def result(self):
        return self._total

    def __str__(self):
        return f'1d20+3={self._total}'


class _StubNpc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label

    def label(self):
        return self._label

    def is_npc(self):
        return True


class _StubPc:
    def __init__(self, uid, label):
        self.entity_uid = uid
        self._label = label
        self.properties = {}
        self.persuasion_calls = []

    def label(self):
        return self._label

    def persuasion_check(self, description=None):
        self.persuasion_calls.append(description)
        return _StubRoll(14)


class _StubGame:
    def __init__(self, entities):
        self._entities = entities
        self.username_to_sid = {}

    def get_entity_by_uid(self, uid):
        return self._entities.get(uid)


class _StubGameWithPov(_StubGame):
    def __init__(self, entities, pov_by_user):
        super().__init__(entities)
        self._pov_by_user = pov_by_user

    def get_pov_entity_for_user(self, username):
        uid = self._pov_by_user.get(username)
        return self._entities.get(uid) if uid else None


class _StubMap:
    def can_see(self, observer, target):
        return True


class _StubGameWithMap(_StubGameWithPov):
    def __init__(self, entities, pov_by_user):
        super().__init__(entities, pov_by_user)
        self._map = _StubMap()

    def get_map_for_entity(self, entity):
        return self._map


class _StubLlmConversation:
    def __init__(self):
        self.messages = []

    def add_message(self, conversation_id, role, content):
        self.messages.append((conversation_id, role, content))


def _build_service(game, llm=None, rag=None, logins=None, entity_owners=None, entities_controlled_by=None):
    return ConversationService(
        current_game_getter=lambda: game,
        game_session=None,
        socketio=None,
        entity_rag_handler_getter=lambda: rag,
        llm_conversation_handler_getter=lambda: llm,
        roles_for_username_getter=lambda: (lambda username: ['player']),
        entity_owners_getter=lambda: (entity_owners or (lambda _entity: [])),
        entities_controlled_by_getter=lambda: (entities_controlled_by or (lambda _username: [])),
        logins_getter=lambda: logins or [],
        logger=logging.getLogger('test_player_skill_check'),
    )


def test_skill_check_reply_key_is_stable():
    assert skill_check_reply_key('abc123') == skill_check_reply_key('abc123')
    assert skill_check_reply_key('abc123') != skill_check_reply_key('xyz')


def test_is_npc_llm_failure_response_detects_provider_errors():
    assert is_npc_llm_failure_response('Error communicating with llama.cpp: 400')
    assert not is_npc_llm_failure_response('Oh, you are a demanding one today.')


def test_whisper_target_hears_message_even_when_marked_visual_only():
    pip = _StubNpc('pip_barmaid', 'Pip')
    aldric = _StubPc('aldric', 'Aldric')
    game = _StubGameWithPov({'pip_barmaid': pip, 'aldric': aldric}, {'player1': 'aldric'})
    service = _build_service(
        game,
        logins=[{'name': 'player1', 'role': ['player']}],
        entities_controlled_by=lambda username: [aldric] if username == 'player1' else [],
    )

    payload = service.conversation_payload(
        pip,
        "You're getting awfully bold today, aren't you?",
        targets=[aldric],
        volume='whisper',
        visual_only_usernames=['player1'],
    )

    rendered = service.render_conversation_payload_for_username('player1', pip, payload)
    assert rendered['message'] == "You're getting awfully bold today, aren't you?"
    assert not rendered.get('visual_only')


def test_whisper_target_excluded_from_visible_overhear_list():
    pip = _StubNpc('pip_barmaid', 'Pip')
    aldric = _StubPc('aldric', 'Aldric')
    bystander = _StubPc('rose_listener', 'Rose')
    game = _StubGameWithMap(
        {'pip_barmaid': pip, 'aldric': aldric, 'rose_listener': bystander},
        {'player1': 'aldric', 'player2': 'rose_listener'},
    )
    game.username_to_sid = {'player1': ['sid-1'], 'player2': ['sid-2']}
    service = _build_service(
        game,
        logins=[
            {'name': 'player1', 'role': ['player']},
            {'name': 'player2', 'role': ['player']},
        ],
        entities_controlled_by=lambda username: {
            'player1': [aldric],
            'player2': [bystander],
        }.get(username, []),
    )

    visible = service.conversation_visible_whisper_usernames(
        pip,
        audible_usernames={'dm'},
        targets=[aldric],
    )

    assert 'player1' not in visible
    assert 'player2' in visible


def test_handle_player_skill_check_button_request_rolls_and_triggers_npc_reply():
    pip = _StubNpc('pip_barmaid', 'Pip')
    aldric = _StubPc('aldric', 'Aldric')
    game = _StubGame({'pip_barmaid': pip, 'aldric': aldric})
    llm = _StubLlmConversation()
    service = _build_service(game, llm=llm, rag=type('R', (), {'_log_social_check': lambda *a, **k: None})())

    npc_reply_called = {'count': 0, 'inject': None}

    def fake_reply_turn(*args, **kwargs):
        npc_reply_called['count'] += 1
        npc_reply_called['inject'] = kwargs.get('inject_system_messages')
        return {'response': 'Fine, have it your way.'}

    service._run_single_npc_reply_turn = fake_reply_turn

    response, status = service.handle_player_skill_check_button_request(
        aldric,
        {
            'requester_uid': 'pip_barmaid',
            'skill': 'persuasion',
            'reply_id': 'reply-1',
            'dc': 12,
        },
        conversation_system_prompt='{name}',
        requesting_username='aldric',
    )

    assert status == 200
    assert response['success'] is True
    assert response['skill_check']['roll_total'] == 14
    assert response['skill_check']['success'] is True
    assert aldric.persuasion_calls
    assert npc_reply_called['count'] == 1
    assert npc_reply_called['inject']
    assert 'Persuasion' in npc_reply_called['inject'][0]
    assert 'npc_reply_failed' not in response

    response2, status2 = service.handle_player_skill_check_button_request(
        aldric,
        {
            'requester_uid': 'pip_barmaid',
            'skill': 'persuasion',
            'reply_id': 'reply-1',
        },
        conversation_system_prompt='{name}',
        requesting_username='aldric',
    )
    assert status2 == 200
    assert response2.get('already_checked') is True
    assert response2['skill_check']['roll_total'] == 14
