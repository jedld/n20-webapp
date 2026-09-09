import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'templates'))
os.environ.setdefault('TEMPLATE_DIR', template_root)

from webapp import app as app_module


class FakeListener:
    def __init__(self, entity_uid='player_pc', name='Aldric'):
        self.entity_uid = entity_uid
        self._name = name
        self.conversation_buffer = []
        self.dialog = False

    def label(self):
        return self._name

    def is_npc(self):
        return False

    def languages(self):
        return ['common']

    def receive_conversation(self, source, message, language=None, directed_to=None, narrative=None, actions=None):
        self.conversation_buffer.append({
            'source': source,
            'message': message,
            'language': language,
        })


class FakeNpcSpeaker:
    def __init__(self, entity_uid='corin_guard', name='Corin Guard'):
        self.entity_uid = entity_uid
        self._name = name
        self.conversation_buffer = []
        self.memory_buffer = []
        self.dialog = True

    def is_npc(self):
        return True

    def label(self):
        return self._name

    def languages(self):
        return ['common']

    def send_conversation(self, message, distance_ft=30, targets=None, language=None, volume=None, narrative=None, actions=None):
        listener = FakeListener()
        self.conversation_buffer.append({
            'source': self,
            'message': message,
            'directed_to': targets,
            'language': language,
        })
        listener.receive_conversation(self, message, language=language, directed_to=targets)
        return [(listener, message, targets or [])]


class FakeBattleMap:
    def __init__(self, entities):
        self.entities = list(entities)
        self.name = 'town_market'


class FakeCurrentGame:
    def __init__(self, speaker, npcs=None):
        self._speaker = speaker
        self._entities = {speaker.entity_uid: speaker}
        self.username_to_sid = {
            'dm': ['sid-dm'],
            'player1': ['sid-player1'],
        }
        for npc in npcs or []:
            self._entities[npc.entity_uid] = npc
        self.battle_map = FakeBattleMap([speaker, *(npcs or [])])

    def get_entity_by_uid(self, entity_uid):
        return self._entities.get(entity_uid)

    def get_map_for_entity(self, entity):
        return self.battle_map

    def increment_game_time(self, entity):
        return None

    def get_pov_entity_for_user(self, username):
        return None


class RecordingConversationHandler:
    def __init__(self):
        self.generated_ids = []

    def generate_response(self, conversation_id):
        self.generated_ids.append(conversation_id)
        return 'Unexpected NPC reply'


class FakeEntityRagHandler:
    def get_nearby_entities(self, entity, distance_ft, volume=None, include_extended=False):
        return []

    def collect_perceivable_targets(self, observer, *, range_ft=120, include_objects=True):
        return []


class FakeGameSession:
    def entity_by_uid(self, entity_uid):
        return None


class EmitRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event_name, payload, to=None, **kwargs):
        self.calls.append({'event_name': event_name, 'payload': payload, 'to': to})


def _configure_client(monkeypatch, speaker, npcs=None):
    handler = RecordingConversationHandler()
    monkeypatch.setattr(app_module, 'current_game', FakeCurrentGame(speaker, npcs=npcs))
    monkeypatch.setattr(app_module, 'llm_conversation_handler', handler)
    monkeypatch.setattr(app_module, 'entity_rag_handler', FakeEntityRagHandler())
    monkeypatch.setattr(app_module, 'game_session', FakeGameSession())
    monkeypatch.setattr(app_module, 'entity_owners', lambda entity: [])
    monkeypatch.setattr(app_module, 'LOGINS', [
        {'name': 'dm', 'role': ['dm']},
        {'name': 'player1', 'role': ['player']},
    ])
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client(), handler


def test_dm_impersonation_records_npc_speech_without_llm_reply(monkeypatch):
    speaker = FakeNpcSpeaker()
    emit_recorder = EmitRecorder()
    client, handler = _configure_client(monkeypatch, speaker)
    monkeypatch.setattr(app_module.socketio, 'emit', emit_recorder)

    with client.session_transaction() as sess:
        sess['username'] = 'dm'

    response = client.post('/talk', json={
        'entity_id': 'corin_guard',
        'message': 'Move along, citizen.',
        'language': 'common',
        'targets': [],
        'dm_impersonate': True,
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert body.get('dm_impersonate') is True
    assert body.get('replies') in (None, [])
    assert handler.generated_ids == []
    assert speaker.conversation_buffer
    assert speaker.conversation_buffer[-1]['message'] == 'Move along, citizen.'
    assert speaker.conversation_buffer[-1]['source'] is speaker

    conversation_calls = [
        call for call in emit_recorder.calls
        if call['event_name'] == 'message' and call['payload'].get('type') == 'conversation'
    ]
    assert any(
        call['payload']['message']['entity_id'] == 'corin_guard'
        for call in conversation_calls
    )


def test_player_cannot_use_dm_impersonation(monkeypatch):
    speaker = FakeNpcSpeaker()
    client, _handler = _configure_client(monkeypatch, speaker)

    with client.session_transaction() as sess:
        sess['username'] = 'player1'

    response = client.post('/talk', json={
        'entity_id': 'corin_guard',
        'message': 'Hello',
        'dm_impersonate': True,
    })

    assert response.status_code == 403
    assert speaker.conversation_buffer == []


def test_dm_impersonation_requires_npc_speaker(monkeypatch):
    class FakePcSpeaker(FakeNpcSpeaker):
        def is_npc(self):
            return False

    speaker = FakePcSpeaker(entity_uid='aldric', name='Aldric')
    client, _handler = _configure_client(monkeypatch, speaker)

    with client.session_transaction() as sess:
        sess['username'] = 'dm'

    response = client.post('/talk', json={
        'entity_id': 'aldric',
        'message': 'Hello',
        'dm_impersonate': True,
    })

    assert response.status_code == 400
    assert speaker.conversation_buffer == []


def test_list_impersonatable_npcs_for_dm(monkeypatch):
    speaker = FakeNpcSpeaker()
    other_npc = FakeNpcSpeaker(entity_uid='merchant', name='Merchant')
    client, _handler = _configure_client(monkeypatch, speaker, npcs=[other_npc])

    with client.session_transaction() as sess:
        sess['username'] = 'dm'

    response = client.get('/conversation_presence', query_string={'list_impersonatable': '1'})
    assert response.status_code == 200
    body = response.get_json()
    npc_ids = {entry['id'] for entry in body.get('impersonatable_npcs', [])}
    assert 'corin_guard' in npc_ids
    assert 'merchant' in npc_ids


def test_list_impersonatable_npcs_forbidden_for_player(monkeypatch):
    speaker = FakeNpcSpeaker()
    client, _handler = _configure_client(monkeypatch, speaker)

    with client.session_transaction() as sess:
        sess['username'] = 'player1'

    response = client.get('/conversation_presence', query_string={'list_impersonatable': '1'})
    assert response.status_code == 403


class PolishConversationHandler(RecordingConversationHandler):
    def __init__(self):
        super().__init__()
        self.polished = 'Move along, citizen — and keep your hands where I can see them.'

    def _effective_send_message(self, messages, context=None):
        return self.polished


def test_dm_impersonation_polish_only_returns_rewritten_line(monkeypatch):
    speaker = FakeNpcSpeaker()
    client, _handler = _configure_client(monkeypatch, speaker)
    monkeypatch.setattr(app_module, 'llm_conversation_handler', PolishConversationHandler())

    with client.session_transaction() as sess:
        sess['username'] = 'dm'

    response = client.post('/talk', json={
        'entity_id': 'corin_guard',
        'message': 'Move along citizen.',
        'dm_impersonate': True,
        'dm_impersonate_polish': True,
        'polish_only': True,
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert body['draft_message'] == 'Move along citizen.'
    assert 'hands where I can see them' in body['polished_message']
    assert speaker.conversation_buffer == []
