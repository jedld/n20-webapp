"""Streamable HTTP MCP protocol, auth, and stdio-proxy tests."""

from __future__ import annotations

import io
import json

import pytest
from flask import Flask

from webapp.mcp.context import MCPContext
from webapp.mcp.protocol import (
    HEADER_MISMATCH,
    PROTOCOL_2025_11,
    PROTOCOL_2026,
    SERVER_NAME,
    handle_jsonrpc,
    protocol_tool_list,
)
from webapp.mcp.routes import register_mcp_blueprint
from webapp.mcp.stdio import build_forward_headers, forward_message, serve_stdio
from webapp.mcp.tool_registry import Tool, ToolRegistry

TOKEN = 'test-mcp-token'


def _registry():
    registry = ToolRegistry()

    def echo(value: str = 'ok'):
        return {'echo': value}

    registry.register(Tool(
        name='world.echo',
        description='Echo a value.',
        handler=echo,
        input_schema={
            'type': 'object',
            'properties': {'value': {'type': 'string'}},
        },
        category='world',
    ))
    return registry


def _context():
    return MCPContext(
        game_session_getter=lambda: None,
        current_game_getter=lambda: None,
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('N20_MCP_DM_TOKEN', TOKEN)
    monkeypatch.setattr(
        'webapp.mcp.routes.get_allowed_origins',
        lambda: ['http://localhost:5001', 'http://127.0.0.1:5001'],
    )
    flask_app = Flask(__name__)
    register_mcp_blueprint(
        flask_app,
        _context(),
        registry=_registry(),
        user_role_fn=lambda: [],
    )
    flask_app.config['TESTING'] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _rpc(method, params=None, msg_id=1, **extra):
    body = {'jsonrpc': '2.0', 'id': msg_id, 'method': method}
    if params is not None:
        body['params'] = params
    body.update(extra)
    return body


def _headers(method, name=None, version=PROTOCOL_2026, token=TOKEN, origin=None,
             bearer=True):
    headers = {
        'Content-Type': 'application/json',
        'Mcp-Method': method,
        'MCP-Protocol-Version': version,
    }
    if name:
        headers['Mcp-Name'] = name
    if bearer and token:
        headers['Authorization'] = f'Bearer {token}'
    elif token:
        headers['X-MCP-Token'] = token
    if origin:
        headers['Origin'] = origin
    return headers


# ---------------------------------------------------------------------------
# Protocol dispatcher (no Flask)
# ---------------------------------------------------------------------------

def test_handle_initialize_and_discover():
    registry = _registry()
    init = handle_jsonrpc(_rpc('initialize', {
        'protocolVersion': PROTOCOL_2025_11,
        'capabilities': {},
        'clientInfo': {'name': 'test', 'version': '0'},
    }), registry)
    assert init['result']['protocolVersion'] == PROTOCOL_2025_11
    assert init['result']['serverInfo']['name'] == SERVER_NAME
    assert 'tools' in init['result']['capabilities']

    discover = handle_jsonrpc(_rpc('server/discover', {}), registry, headers={
        'mcp-method': 'server/discover',
        'mcp-protocol-version': PROTOCOL_2026,
    })
    assert PROTOCOL_2026 in discover['result']['supportedVersions']
    assert discover['result']['capabilities']['tools']['listChanged'] is False


def test_handle_tools_list_and_call():
    registry = _registry()
    listed = handle_jsonrpc(_rpc('tools/list'), registry)
    names = [t['name'] for t in listed['result']['tools']]
    assert names == ['world.echo']
    assert 'category' not in listed['result']['tools'][0]
    assert listed['result']['ttlMs'] == 30_000

    called = handle_jsonrpc(_rpc('tools/call', {
        'name': 'world.echo',
        'arguments': {'value': 'hi'},
    }), registry)
    assert called['result']['isError'] is False
    assert called['result']['structuredContent'] == {'echo': 'hi'}
    assert called['result']['content'][0]['type'] == 'text'


def test_handle_2026_header_mismatch():
    registry = _registry()
    response = handle_jsonrpc(
        _rpc('tools/list'),
        registry,
        headers={
            'mcp-protocol-version': PROTOCOL_2026,
            'mcp-method': 'tools/call',
        },
    )
    assert response['error']['code'] == HEADER_MISMATCH


def test_handle_notification_returns_none():
    registry = _registry()
    assert handle_jsonrpc({
        'jsonrpc': '2.0',
        'method': 'notifications/initialized',
        'params': {},
    }, registry) is None


def test_protocol_tool_list_strips_category():
    tools = protocol_tool_list(_registry())['tools']
    assert tools[0]['name'] == 'world.echo'
    assert 'category' not in tools[0]


# ---------------------------------------------------------------------------
# Flask Streamable HTTP
# ---------------------------------------------------------------------------

def test_unauth_rpc_returns_401(client, monkeypatch):
    monkeypatch.delenv('N20_MCP_DM_TOKEN', raising=False)
    # Rebuild would be needed if token was required from env at request time;
    # the fixture set the env var, so send no credentials against a set token.
    monkeypatch.setenv('N20_MCP_DM_TOKEN', TOKEN)
    resp = client.post('/mcp', json=_rpc('ping'))
    assert resp.status_code == 401
    body = resp.get_json()
    assert body['error']['message'] == 'Unauthorized'


def test_bearer_token_initialize(client):
    resp = client.post(
        '/mcp',
        json=_rpc('initialize', {
            'protocolVersion': PROTOCOL_2025_11,
            'capabilities': {},
            'clientInfo': {'name': 'cursor', 'version': '1'},
        }),
        headers=_headers('initialize', version=PROTOCOL_2025_11),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['result']['protocolVersion'] == PROTOCOL_2025_11
    assert body['result']['serverInfo']['name'] == SERVER_NAME


def test_x_mcp_token_discover(client):
    resp = client.post(
        '/mcp',
        json=_rpc('server/discover', {}),
        headers=_headers('server/discover', token=TOKEN, bearer=False),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert PROTOCOL_2026 in body['result']['supportedVersions']


def test_tools_list_and_call_via_http(client):
    listed = client.post(
        '/mcp',
        json=_rpc('tools/list'),
        headers=_headers('tools/list', version=PROTOCOL_2025_11),
    )
    assert listed.status_code == 200
    tools = listed.get_json()['result']['tools']
    assert tools[0]['name'] == 'world.echo'

    called = client.post(
        '/mcp',
        json=_rpc('tools/call', {'name': 'world.echo', 'arguments': {'value': 'n20'}}),
        headers=_headers('tools/call', name='world.echo', version=PROTOCOL_2026),
    )
    assert called.status_code == 200
    result = called.get_json()['result']
    assert result['structuredContent'] == {'echo': 'n20'}
    assert result['isError'] is False


def test_header_mismatch_via_http(client):
    resp = client.post(
        '/mcp',
        json=_rpc('tools/call', {'name': 'world.echo', 'arguments': {}}),
        headers=_headers('tools/list', name='world.echo', version=PROTOCOL_2026),
    )
    assert resp.status_code == 200
    assert resp.get_json()['error']['code'] == HEADER_MISMATCH


def test_origin_forbidden(client):
    resp = client.post(
        '/mcp',
        json=_rpc('ping'),
        headers=_headers('ping', version=PROTOCOL_2025_11, origin='https://evil.example'),
    )
    assert resp.status_code == 403
    assert resp.get_json()['error']['message'] == 'Forbidden origin'


def test_origin_allowed_localhost(client):
    resp = client.post(
        '/mcp',
        json=_rpc('ping'),
        headers=_headers('ping', version=PROTOCOL_2025_11, origin='http://localhost:5001'),
    )
    assert resp.status_code == 200
    assert resp.get_json()['result'] == {}


def test_notification_returns_202(client):
    resp = client.post(
        '/mcp',
        json={
            'jsonrpc': '2.0',
            'method': 'notifications/initialized',
            'params': {},
        },
        headers=_headers('notifications/initialized', version=PROTOCOL_2025_11),
    )
    assert resp.status_code == 202
    assert resp.data == b''


def test_rpc_accepts_trailing_slash(client):
    resp = client.post(
        '/mcp/',
        json=_rpc('ping'),
        headers=_headers('ping', version=PROTOCOL_2025_11),
    )
    assert resp.status_code == 200


def test_legacy_rest_still_works(client):
    listed = client.get('/mcp/tools/list', headers={'Authorization': f'Bearer {TOKEN}'})
    assert listed.status_code == 200
    assert listed.get_json()['tools'][0]['name'] == 'world.echo'

    called = client.post(
        '/mcp/tools/call',
        json={'name': 'world.echo', 'arguments': {'value': 'rest'}},
        headers={'X-MCP-Token': TOKEN},
    )
    assert called.status_code == 200
    envelope = called.get_json()
    assert envelope['isError'] is False
    assert envelope['content'][0]['type'] == 'json'
    assert envelope['content'][0]['json'] == {'echo': 'rest'}


def test_legacy_unauth(client):
    resp = client.get('/mcp/manifest')
    assert resp.status_code == 401
    assert resp.get_json()['isError'] is True


def test_dm_session_bypasses_token(monkeypatch):
    monkeypatch.delenv('N20_MCP_DM_TOKEN', raising=False)
    monkeypatch.setattr(
        'webapp.mcp.routes.get_allowed_origins',
        lambda: ['http://localhost:5001'],
    )
    flask_app = Flask(__name__)
    register_mcp_blueprint(
        flask_app,
        _context(),
        registry=_registry(),
        user_role_fn=lambda: ['dm'],
    )
    with flask_app.test_client() as c:
        resp = c.post('/mcp', json=_rpc('ping'))
        assert resp.status_code == 200
        assert resp.get_json()['result'] == {}


# ---------------------------------------------------------------------------
# stdio proxy
# ---------------------------------------------------------------------------

def test_stdio_build_forward_headers_mirrors_method_and_token(monkeypatch):
    monkeypatch.setenv('N20_MCP_DM_TOKEN', TOKEN)
    headers = build_forward_headers(_rpc('tools/call', {
        'name': 'world.echo',
        'arguments': {},
        '_meta': {'io.modelcontextprotocol/protocolVersion': PROTOCOL_2026},
    }))
    assert headers['Mcp-Method'] == 'tools/call'
    assert headers['Mcp-Name'] == 'world.echo'
    assert headers['MCP-Protocol-Version'] == PROTOCOL_2026
    assert headers['Authorization'] == f'Bearer {TOKEN}'
    assert headers['X-MCP-Token'] == TOKEN


def test_stdio_forward_message_posts_json():
    captured = {}

    def fake_post(url, body, headers, timeout):
        captured['url'] = url
        captured['body'] = json.loads(body.decode('utf-8'))
        captured['headers'] = headers
        return 200, json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'result': {'ok': True},
        })

    response = forward_message(
        _rpc('ping'),
        url='http://example.test/mcp',
        post=fake_post,
    )
    assert captured['url'] == 'http://example.test/mcp'
    assert captured['body']['method'] == 'ping'
    assert response['result'] == {'ok': True}


def test_stdio_serve_loop_writes_response():
    stdin = io.StringIO(json.dumps(_rpc('ping')) + '\n')
    stdout = io.StringIO()

    def fake_post(url, body, headers, timeout):
        parsed = json.loads(body.decode('utf-8'))
        return 200, json.dumps({
            'jsonrpc': '2.0',
            'id': parsed['id'],
            'result': {},
        })

    serve_stdio(stdin=stdin, stdout=stdout, stderr=io.StringIO(), post=fake_post)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln]
    assert len(lines) == 1
    assert json.loads(lines[0])['result'] == {}


def test_stdio_notification_writes_nothing():
    stdin = io.StringIO(json.dumps({
        'jsonrpc': '2.0',
        'method': 'notifications/initialized',
        'params': {},
    }) + '\n')
    stdout = io.StringIO()

    def fake_post(url, body, headers, timeout):
        return 202, ''

    serve_stdio(stdin=stdin, stdout=stdout, stderr=io.StringIO(), post=fake_post)
    assert stdout.getvalue() == ''
