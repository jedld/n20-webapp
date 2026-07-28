"""Unit tests for player-side Perception checks in conversation."""

import logging
import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

import importlib

conversation_service = importlib.import_module('webapp.conversation_service')

from webapp.conversation_service import (
    ConversationService,
    is_player_perception_request,
    normalize_player_dm_command,
    parse_player_perception_request,
    perception_check_message_key,
)


def test_recognises_basic_perception_phrasings():
    assert is_player_perception_request('Perception check')
    assert is_player_perception_request('perception check on @Pip')
    assert is_player_perception_request('roll perception on the barmaid')
    assert is_player_perception_request('/perception')
    assert is_player_perception_request('@dm perception on Pip')
    assert is_player_perception_request('@DM perception on @pip-barmaid')


def test_normalize_player_dm_command_strips_prefix():
    assert normalize_player_dm_command('@dm perception on Pip') == 'perception on Pip'
    assert normalize_player_dm_command('  @DM   perception on Pip  ') == 'perception on Pip'


def test_parse_dm_perception_command_extracts_target():
    target, focus = parse_player_perception_request('@dm perception on Pip about her apron')
    assert target == 'Pip'
    assert focus == 'her apron'


def test_does_not_match_unrelated_messages():
    assert not is_player_perception_request('I have keen perception.')
    assert not is_player_perception_request('')


def test_parse_extracts_target_and_focus():
    target, focus = parse_player_perception_request(
        'Perception check on @Pip about her hands'
    )
    assert target == '@Pip'
    assert focus == 'her hands'


class _StubRoll:
    def __init__(self, total):
        self._total = total

    def result(self):
        return self._total

    def __str__(self):
        return f'1d20+2={self._total}'


class _StubEntity:
    def __init__(self, uid, label, npc=False, memory=None):
        self.entity_uid = uid
        self._label = label
        self._npc = npc
        self.memory_buffer = memory or []
        self.perception_calls = []
        self.properties = {}

    def label(self):
        return self._label

    def is_npc(self):
        return self._npc

    def perception_check(self, description=None):
        self.perception_calls.append(description)
        return _StubRoll(16)


class _StubRagHandler:
    def __init__(self, verdict=None, target=None):
        self.verdict = verdict or {
            'description': 'You notice a smudge of flour on her sleeve.',
        }
        self.target = target
        self.eval_calls = []
        self.log_calls = []

    def resolve_named_target(self, actor, target_spec, speaker=None, include_objects=False):
        if self.target is not None and target_spec:
            return self.target
        return None

    def _evaluate_perception_description(self, observer, target, focus, roll, llm_conversation_handler):
        self.eval_calls.append({
            'observer': observer,
            'target': target,
            'focus': focus,
            'roll_total': roll.result(),
        })
        return self.verdict

    def _log_social_check(self, message, entities=None):
        self.log_calls.append((message, entities))


def _make_service(speaker, target, rag_handler=None, *, can_see=True):
    emitted = []

    class _StubMap:
        def can_see(self, observer, subject, **kwargs):
            return can_see

    class _StubGame:
        username_to_sid = {'alice': ['sid-alice'], 'dm': ['sid-dm']}

        def get_entity_by_uid(self, uid):
            for entity in (speaker, target):
                if entity is not None and entity.entity_uid == uid:
                    return entity
            return None

        def get_map_for_entity(self, entity):
            return _StubMap()

    class _StubSocketio:
        def emit(self, event, payload, to=None):
            emitted.append({'event': event, 'payload': payload, 'to': to})

    service = ConversationService(
        current_game_getter=lambda: _StubGame(),
        game_session=None,
        socketio=_StubSocketio(),
        entity_rag_handler_getter=lambda: rag_handler,
        llm_conversation_handler_getter=lambda: object(),
        roles_for_username_getter=lambda: (lambda username: ['dm'] if username == 'dm' else ['player']),
        entity_owners_getter=lambda: (lambda entity: ['alice'] if entity is speaker else []),
        entities_controlled_by_getter=lambda: (lambda username: [speaker] if username == 'alice' else []),
        logins_getter=lambda: [
            {'name': 'alice', 'role': ['player']},
            {'name': 'dm', 'role': ['dm']},
        ],
        logger=logging.getLogger('test_player_perception'),
    )
    return service, emitted, rag_handler


def test_handle_request_rolls_and_describes_scene():
    npc = _StubEntity('pip_barmaid', 'Pip', npc=True)
    speaker = _StubEntity(
        'aldric',
        'Aldric',
        memory=[{'source': npc, 'message': 'Can I help you?'}],
    )
    rag = _StubRagHandler(target=npc)
    service, emitted, _ = _make_service(speaker, target=npc, rag_handler=rag)

    response, status = service.handle_player_perception_request(
        speaker,
        'Perception check on @Pip about her apron',
        explicit_targets=[npc],
        mentioned_targets=[npc],
    )

    assert status == 200
    assert response['success'] is True
    assert response['perception']['roll_total'] == 16
    assert 'flour' in response['perception']['description']
    assert speaker.perception_calls
    assert rag.eval_calls
    private_payload = next(
        evt['payload']['message'] for evt in emitted
        if evt['payload']['message'].get('perception_check', {}).get('description')
    )
    assert private_payload['perception_check']['description']


def test_perception_check_message_key_is_stable():
    key_a = perception_check_message_key('pip', 'apron')
    key_b = perception_check_message_key('pip', 'apron')
    key_c = perception_check_message_key('pip', 'hands')
    assert key_a == key_b
    assert key_a != key_c


def test_handle_perception_button_request_dedupes():
    npc = _StubEntity('pip_barmaid', 'Pip', npc=True)
    speaker = _StubEntity('aldric', 'Aldric')
    rag = _StubRagHandler()
    service, _, _ = _make_service(speaker, target=npc, rag_handler=rag)

    response, status = service.handle_player_perception_button_request(
        speaker,
        {'target_id': 'pip_barmaid', 'focus': 'scene'},
    )
    assert status == 200
    assert response['success'] is True

    response2, status2 = service.handle_player_perception_button_request(
        speaker,
        {'target_id': 'pip_barmaid', 'focus': 'scene'},
    )
    assert status2 == 200
    assert response2.get('already_checked') is True
    assert len(speaker.perception_calls) == 1


def test_dm_perception_command_in_local_chat():
    npc = _StubEntity('pip_barmaid', 'Pip', npc=True)
    speaker = _StubEntity('aldric', 'Aldric')
    rag = _StubRagHandler(target=npc)
    service, emitted, _ = _make_service(speaker, target=npc, rag_handler=rag)

    response, status = service.handle_player_perception_request(
        speaker,
        '@dm perception on Pip',
        explicit_targets=[],
        mentioned_targets=[],
    )

    assert status == 200
    assert response['success'] is True
    assert 'flour' in response['perception']['description']
    assert speaker.perception_calls


def test_perception_blocked_when_target_not_visible():
    npc = _StubEntity('pip_barmaid', 'Pip', npc=True)
    speaker = _StubEntity('aldric', 'Aldric')
    rag = _StubRagHandler(target=npc)
    emitted = []

    class _StubMap:
        def can_see(self, observer, subject, **kwargs):
            return False

    class _StubGame:
        username_to_sid = {'alice': ['sid-alice']}

        def get_map_for_entity(self, entity):
            return _StubMap()

    service = ConversationService(
        current_game_getter=lambda: _StubGame(),
        game_session=None,
        socketio=type('Sock', (), {'emit': lambda *a, **k: emitted.append(k)})(),
        entity_rag_handler_getter=lambda: rag,
        llm_conversation_handler_getter=lambda: object(),
        roles_for_username_getter=lambda: (lambda username: ['player']),
        entity_owners_getter=lambda: (lambda entity: ['alice'] if entity is speaker else []),
        entities_controlled_by_getter=lambda: (lambda username: [speaker] if username == 'alice' else []),
        logins_getter=lambda: [{'name': 'alice', 'role': ['player']}],
        logger=logging.getLogger('test_player_perception'),
    )

    response, status = service.handle_player_perception_request(
        speaker,
        '@dm perception on Pip',
    )

    assert status == 200
    assert response['perception'] == 'not_visible'
    assert not speaker.perception_calls
