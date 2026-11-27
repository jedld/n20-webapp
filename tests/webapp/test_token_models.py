"""3D VTT token-model routes (``/dm/token_models*``) + upload helper.

HeroForge-style figures uploaded by the DM land in
``<campaign>/assets/models/tokens/``, auto-convert to GLB when needed, and
are bound to entities through the ``tokens:`` block of ``vtt3d.yml``.
"""

import io
import os
import shutil
import struct
import sys
import zipfile
from unittest.mock import Mock, patch

import pytest
import yaml

WEBAPP_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

ENGINE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
template_root = os.path.join(ENGINE_ROOT, 'templates')
os.environ.setdefault('TEMPLATE_DIR', template_root)

FIXTURES = os.path.join(ENGINE_ROOT, 'tests', 'fixtures')

from webapp.app import app  # noqa: E402
from webapp.blueprints.helpers import runtime_state, token_models  # noqa: E402
from natural20.event_manager import EventManager  # noqa: E402
from natural20.session import Session  # noqa: E402


def _binary_stl_triangle():
    """Smallest valid binary STL: one triangle."""
    tri = struct.pack('<12fH', 0.0, 0.0, 1.0,
                      0.0, 0.0, 0.0,
                      1.0, 0.0, 0.0,
                      0.0, 1.0, 0.0, 0)
    return b'solid tiny'.ljust(80, b' ') + struct.pack('<I', 1) + tri


@pytest.fixture
def world(tmp_path):
    campaign = tmp_path / 'campaign'
    shutil.copytree(FIXTURES, campaign,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    session = Session(root_path=str(campaign), event_manager=EventManager())
    game = Mock()
    game.maps = {}
    return session, game, campaign


def _client(dm=True):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'dm' if dm else 'player'
    return client


def _run(world, fn, dm=True):
    session, game, campaign = world
    socketio = Mock()
    patches = [
        patch.dict(runtime_state._globals, {
            'current_game': game,
            'game_session': session,
            'socketio': socketio,
        }),
        patch('webapp.blueprints.dm.get_socketio', return_value=socketio),
        patch('webapp.blueprints.dm.user_role', new=lambda: ['dm'] if dm else ['player']),
    ]
    for p in patches:
        p.start()
    try:
        return fn(_client(dm), session, campaign, socketio)
    finally:
        for p in patches:
            p.stop()


def _vtt3d_tokens(campaign):
    path = campaign / 'vtt3d.yml'
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return data.get('tokens') or {}


# ---------------------------------------------------------------------------
# helper unit tests
# ---------------------------------------------------------------------------

def test_upload_stl_converts_to_glb(world):
    session, _game, campaign = world
    result = token_models.save_upload(
        session,
        type('F', (), {'filename': 'paladin.stl',
                       'stream': io.BytesIO(_binary_stl_triangle()),
                       'save': lambda self, path: open(path, 'wb').write(_binary_stl_triangle())})(),
    )
    assert 'error' not in result, result
    assert result['converted'] is True
    tokens_dir = campaign / 'assets/models/tokens'
    assert (tokens_dir / 'paladin.stl').is_file()
    glb = tokens_dir / 'paladin.glb'
    assert glb.is_file()
    assert glb.read_bytes()[:4] == b'glTF'
    assert 'models/tokens/paladin.glb' in result['models']


def test_upload_rejects_unknown_extension(world):
    session, _game, _campaign = world
    result = token_models.save_upload(
        session,
        type('F', (), {'filename': 'evil.exe',
                       'stream': io.BytesIO(b'MZ'),
                       'save': lambda self, path: open(path, 'wb').write(b'MZ')})(),
    )
    assert 'error' in result


def test_upload_zip_extracts_models_only(world):
    session, _game, campaign = world
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('figure/model.obj', 'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        zf.writestr('figure/model.mtl', 'newmtl m\n')
        zf.writestr('figure/README.txt', 'ignore me')
    buf.seek(0)
    result = token_models.save_upload(
        session,
        type('F', (), {'filename': 'hero forge figure.zip',
                       'stream': buf,
                       'save': lambda self, path: None})(),
    )
    assert 'error' not in result, result
    tokens_dir = campaign / 'assets/models/tokens'
    names = sorted(p.name for p in tokens_dir.iterdir())
    assert any(n.endswith('.obj') for n in names)
    assert any(n.endswith('.mtl') for n in names)
    assert not any(n.endswith('.txt') for n in names)
    assert result['models'] and result['models'][0].endswith('.obj')


def test_assignments_roundtrip_preserves_other_sections(world):
    session, _game, campaign = world
    (campaign / 'vtt3d.yml').write_text(
        'walls:\n  default: stone\nobjects:\n  crate:\n    kind: crate\n',
        encoding='utf-8',
    )
    saved = token_models.set_assignment(
        session, 'vampire', {'src': '/assets/models/tokens/v.glb', 'height': 8})
    assert saved == {'src': 'models/tokens/v.glb', 'height': 8}
    doc = yaml.safe_load((campaign / 'vtt3d.yml').read_text(encoding='utf-8'))
    assert doc['walls'] == {'default': 'stone'}
    assert doc['objects'] == {'crate': {'kind': 'crate'}}
    assert doc['tokens'] == {'vampire': {'src': 'models/tokens/v.glb', 'height': 8}}

    assert token_models.remove_assignment(session, 'vampire') is True
    doc = yaml.safe_load((campaign / 'vtt3d.yml').read_text(encoding='utf-8'))
    assert 'tokens' not in doc
    assert doc['walls'] == {'default': 'stone'}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_upload_route_requires_dm(world):
    def go(client, _session, campaign, _socketio):
        data = {'model': (io.BytesIO(_binary_stl_triangle()), 'pal.stl')}
        resp = client.post('/dm/token_models/upload', data=data,
                           content_type='multipart/form-data')
        assert resp.status_code == 403
        assert not (campaign / 'assets/models/tokens').exists()
    _run(world, go, dm=False)


def test_upload_assign_and_delete_routes(world):
    def go(client, _session, campaign, socketio):
        data = {'model': (io.BytesIO(_binary_stl_triangle()), 'pal.stl'), 'key': 'paladin'}
        resp = client.post('/dm/token_models/upload', data=data,
                           content_type='multipart/form-data')
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['converted'] is True
        assert _vtt3d_tokens(campaign) == {
            'paladin': {'src': 'models/tokens/pal.glb'}}
        socketio.emit.assert_called_with('message', {'type': 'refresh_map'})

        # assign (re-bind with tweaks)
        resp = client.post('/dm/token_models/assign', json={
            'key': 'npc-vampire-1', 'src': 'models/tokens/pal',
            'height': 7, 'color': '#8899ff', 'rotate_y': 90,
        })
        assert resp.status_code == 200, resp.get_json()
        assert _vtt3d_tokens(campaign)['npc-vampire-1'] == {
            'src': 'models/tokens/pal.glb', 'height': 7,
            'color': '#8899ff', 'rotate_y': 90,
        }

        # unassign
        resp = client.post('/dm/token_models/unassign', json={'key': 'paladin'})
        assert resp.status_code == 200
        assert 'paladin' not in _vtt3d_tokens(campaign)

        # list
        resp = client.get('/dm/token_models')
        body = resp.get_json()
        assert resp.status_code == 200
        srcs = [entry['src'] for entry in body['library']]
        assert 'models/tokens/pal.glb' in srcs
        assert 'models/tokens/pal.stl' in srcs
        assert 'npc-vampire-1' in body['assignments']

        # delete file also drops assignments referencing it
        resp = client.post('/dm/token_models/delete',
                           json={'src': 'models/tokens/pal.glb'})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['unassigned'] == ['npc-vampire-1']
        assert not (campaign / 'assets/models/tokens/pal.glb').exists()

        resp = client.post('/dm/token_models/delete', json={'src': 'models/tokens/pal.glb'})
        assert resp.status_code == 404
    _run(world, go)


def test_assign_missing_src_is_400(world):
    def go(client, _session, _campaign, _socketio):
        resp = client.post('/dm/token_models/assign', json={'key': 'x'})
        assert resp.status_code == 400
    _run(world, go)
