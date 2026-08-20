"""HTTP surface for player/campaign notebooks."""

from __future__ import annotations

import io
from types import SimpleNamespace

from flask import Flask

from natural20.notebook_db import NotebookDB
from webapp.blueprints import notebook as notebook_mod


def _game(tmp_path):
    db = NotebookDB(
        str(tmp_path / 'notebook.sqlite'),
        files_dir=str(tmp_path / 'notebook_files'),
    )
    return SimpleNamespace(
        notebook_db=db,
        username_to_sid={'gomerin': ['sid-g'], 'dm': ['sid-dm']},
    )


def _client(monkeypatch, tmp_path, *, role=('dm',), username='dm', game=None):
    current_game = game or _game(tmp_path)
    emitted = []

    monkeypatch.setattr(notebook_mod, 'logged_in', lambda: bool(username))
    monkeypatch.setattr(notebook_mod, 'user_role', lambda: list(role))
    monkeypatch.setattr(notebook_mod, 'get_current_game', lambda: current_game)
    monkeypatch.setattr(
        notebook_mod,
        'get_socketio',
        lambda: SimpleNamespace(emit=lambda *a, **k: emitted.append((a, k))),
    )
    monkeypatch.setattr(
        notebook_mod,
        'get_logins',
        lambda: [
            {'name': 'dm', 'role': ['dm']},
            {'name': 'gomerin', 'role': ['player']},
        ],
    )
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(notebook_mod.notebook_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        if username:
            sess['username'] = username
    return client, current_game, emitted


def test_player_cannot_read_campaign_notebook(monkeypatch, tmp_path):
    client, _, _ = _client(monkeypatch, tmp_path, role=('player',), username='gomerin')
    resp = client.get('/notebook?scope=campaign')
    assert resp.status_code in (400, 403)


def test_unauthenticated_notebook_rejected(monkeypatch, tmp_path):
    client, _, _ = _client(monkeypatch, tmp_path, username=None)
    with client.session_transaction() as sess:
        sess.pop('username', None)
    monkeypatch.setattr(notebook_mod, 'logged_in', lambda: False)
    resp = client.get('/notebook')
    assert resp.status_code == 401


def test_player_note_crud_and_upload(monkeypatch, tmp_path):
    client, game, _ = _client(monkeypatch, tmp_path, role=('player',), username='gomerin')
    created = client.post('/notebook/items', json={'title': 'Clue', 'content': 'A torn letter'})
    assert created.status_code == 200
    item_id = created.get_json()['item']['id']

    listed = client.get('/notebook?scope=player')
    assert listed.status_code == 200
    items = listed.get_json()['items']
    assert any(row['id'] == item_id for row in items)

    patched = client.patch(f'/notebook/items/{item_id}', json={'content': 'A torn letter signed Ireena'})
    assert patched.status_code == 200
    assert 'Ireena' in patched.get_json()['item']['content']

    folder = client.post('/notebook/folders', json={'name': 'Letters'})
    assert folder.status_code == 200
    folder_id = folder.get_json()['folder']['id']
    moved = client.post('/notebook/move', json={'type': 'item', 'id': item_id, 'folder_id': folder_id})
    assert moved.status_code == 200

    uploaded = client.post(
        '/notebook/items',
        data={'file': (io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'\x00' * 24), 'mark.png')},
    )
    assert uploaded.status_code == 200
    assert uploaded.get_json()['item']['kind'] == 'file'


def test_share_emits_to_recipient(monkeypatch, tmp_path):
    client, game, emitted = _client(monkeypatch, tmp_path, role=('dm',), username='dm')
    created = client.post(
        '/notebook/items',
        json={'scope': 'campaign', 'title': 'Handout', 'content': 'Show this map.'},
    )
    item_id = created.get_json()['item']['id']
    shared = client.post(f'/notebook/items/{item_id}/share', json={'usernames': ['gomerin']})
    assert shared.status_code == 200
    share_id = shared.get_json()['shares'][0]['id']
    assert any(
        args and args[0] == 'message' and args[1].get('type') == 'notebook_share'
        for args, _kwargs in emitted
    )

    player, _, _ = _client(monkeypatch, tmp_path, role=('player',), username='gomerin', game=game)
    view = player.get(f'/notebook/shared/{share_id}')
    assert view.status_code == 200
    assert 'Show this map' in view.get_json()['item']['content']


def test_map_link_visible_only_to_owner(monkeypatch, tmp_path):
    client, game, _ = _client(monkeypatch, tmp_path, role=('dm',), username='dm')
    created = client.post(
        '/notebook/items',
        json={'scope': 'campaign', 'title': 'Secret clue', 'content': 'Behind the bar.'},
    )
    item_id = created.get_json()['item']['id']
    pinned = client.post(
        '/notebook/map_links',
        json={'item_id': item_id, 'map_name': 'tavern', 'x': 4, 'y': 6},
    )
    assert pinned.status_code == 200
    listed = client.get('/notebook/map_links?map_name=tavern')
    assert listed.status_code == 200
    links = listed.get_json()['links']
    assert len(links) == 1
    assert links[0]['item_id'] == item_id
    assert links[0]['title'] == 'Secret clue'

    player, _, _ = _client(monkeypatch, tmp_path, role=('player',), username='gomerin', game=game)
    other = player.get('/notebook/map_links?map_name=tavern')
    assert other.status_code == 200
    assert other.get_json()['links'] == []
