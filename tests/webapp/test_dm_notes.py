"""DM-only map notes HTTP surface."""

from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from natural20.dm_notes import list_notes
from webapp.blueprints import dm as dm_mod


def _game(session_state=None):
    game_session = SimpleNamespace(session_state=session_state if session_state is not None else {}, game_time=0)
    battle_map = SimpleNamespace(name='tavern')
    return SimpleNamespace(
        game_session=game_session,
        maps={'tavern': battle_map, 'cellar': SimpleNamespace(name='cellar')},
        get_map_for_user=lambda username: battle_map,
    )


def _client(monkeypatch, *, role=('dm',), username='dm', game=None):
    current_game = game or _game()
    monkeypatch.setattr(dm_mod, 'user_role', lambda: list(role))
    monkeypatch.setattr(dm_mod, 'get_current_game', lambda: current_game)
    monkeypatch.setattr(dm_mod, 'get_socketio', lambda: SimpleNamespace(emit=lambda *a, **k: None))
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(dm_mod.dm_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        if username:
            sess['username'] = username
    return client, current_game


def test_dm_notes_forbidden_for_player(monkeypatch):
    client, _ = _client(monkeypatch, role=('player',), username='gomerin')
    resp = client.get('/dm/notes')
    assert resp.status_code == 403
    assert resp.get_json().get('error') == 'Forbidden'


def test_dm_notes_unauthorized_without_login(monkeypatch):
    client, _ = _client(monkeypatch, username=None)
    with client.session_transaction() as sess:
        sess.pop('username', None)
    resp = client.get('/dm/notes')
    assert resp.status_code == 401


def test_dm_notes_crud_roundtrip(monkeypatch):
    client, game = _client(monkeypatch)
    created = client.post('/dm/notes', json={'x': 8, 'y': 12, 'text': 'Ambush behind the crates'})
    assert created.status_code == 200
    body = created.get_json()
    assert body['success'] is True
    assert body['map'] == 'tavern'
    note_id = body['note']['id']
    assert body['note']['text'] == 'Ambush behind the crates'

    listed = client.get('/dm/notes')
    assert listed.status_code == 200
    notes = listed.get_json()['notes']
    assert len(notes) == 1
    assert notes[0]['id'] == note_id

    moved = client.post('/dm/notes/move', json={'id': note_id, 'x': 9, 'y': 12})
    assert moved.status_code == 200
    assert moved.get_json()['note']['x'] == 9

    deleted = client.delete(f'/dm/notes/{note_id}')
    assert deleted.status_code == 200
    assert list_notes(game.game_session, 'tavern') == []


def test_player_cannot_create_dm_note(monkeypatch):
    client, game = _client(monkeypatch, role=('player',), username='gomerin')
    resp = client.post('/dm/notes', json={'x': 1, 'y': 1, 'text': 'should not persist'})
    assert resp.status_code == 403
    assert list_notes(game.game_session, 'tavern') == []
