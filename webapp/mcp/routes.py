"""Flask blueprint exposing the MCP tool surface.

Legacy REST (in-app LLM / scripts):

- ``GET  /mcp/manifest``       — server metadata + tool count.
- ``GET  /mcp/tools/list``     — full tool manifest (JSON Schema inputs).
- ``POST /mcp/tools/call``     — body ``{"name": str, "arguments": object}``;
  returns an MCP-style envelope ``{"isError": bool, "content": [...]}``.

Streamable HTTP (Cursor, Claude Code, and other MCP clients):

- ``POST /mcp``                — JSON-RPC 2.0 (spec 2026-07-28, with
  2025-era ``initialize`` fallback). See :mod:`webapp.mcp.protocol`.

Authorisation: requests must either come from a logged-in DM session
(``'dm' in user_role()``), or carry a token matching ``N20_MCP_DM_TOKEN``
via ``Authorization: Bearer <token>`` or ``X-MCP-Token``. If the env var
is unset, only DM session callers are accepted.
"""

from __future__ import annotations

import hmac
import os
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from webapp.blueprints.helpers.cors_config import get_allowed_origins, origin_allowed

from .context import MCPContext
from .protocol import handle_jsonrpc
from .tool_registry import ToolRegistry
from . import tools_actions, tools_dm, tools_npc, tools_world


def build_default_registry() -> ToolRegistry:
    """Create a registry pre-populated with all stock MCP tool modules."""
    registry = ToolRegistry()
    tools_world.register(registry)
    tools_dm.register(registry)
    tools_actions.register(registry)
    tools_npc.register(registry)
    return registry


def register_mcp_blueprint(app, context: MCPContext,
                           registry: Optional[ToolRegistry] = None,
                           user_role_fn: Optional[Callable[[], object]] = None,
                           url_prefix: str = '/mcp') -> Blueprint:
    """Build, configure, and register the ``/mcp`` blueprint on ``app``.

    Returns the blueprint so the caller can introspect it for tests.
    """
    if registry is None:
        registry = build_default_registry()

    bp = Blueprint('mcp', __name__, url_prefix=url_prefix)

    def _expected_token() -> str:
        return os.environ.get('N20_MCP_DM_TOKEN', '').strip()

    def _token_matches(supplied: str, expected: str) -> bool:
        if not supplied or not expected:
            return False
        if len(supplied) != len(expected):
            return False
        return hmac.compare_digest(supplied, expected)

    def _authorised() -> bool:
        expected = _expected_token()
        if expected:
            header = request.headers.get('X-MCP-Token', '').strip()
            if _token_matches(header, expected):
                return True
            auth = request.headers.get('Authorization', '').strip()
            scheme, _, rest = auth.partition(' ')
            if scheme.lower() == 'bearer' and _token_matches(rest.strip(), expected):
                return True
        if user_role_fn is not None:
            try:
                role = user_role_fn() or []
                if 'dm' in role:
                    return True
            except Exception:
                pass
        return False

    def _is_rpc_request() -> bool:
        path = (request.path or '').rstrip('/')
        prefix = (url_prefix or '/mcp').rstrip('/') or '/mcp'
        return path == prefix

    def _jsonrpc_id():
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and 'id' in payload:
            return payload.get('id')
        return None

    @bp.before_request
    def _gate():
        # Allow CORS-style preflight without auth.
        if request.method == 'OPTIONS':
            return None

        origin = request.headers.get('Origin', '').strip()
        if origin and not origin_allowed(origin, get_allowed_origins()):
            if _is_rpc_request():
                return jsonify({
                    'jsonrpc': '2.0',
                    'id': _jsonrpc_id(),
                    'error': {'code': -32000, 'message': 'Forbidden origin'},
                }), 403
            return jsonify({
                'isError': True,
                'content': [{'type': 'text', 'text': 'Forbidden origin'}],
            }), 403

        if not _authorised():
            if _is_rpc_request():
                return jsonify({
                    'jsonrpc': '2.0',
                    'id': _jsonrpc_id(),
                    'error': {'code': -32001, 'message': 'Unauthorized'},
                }), 401
            return jsonify({
                'isError': True,
                'content': [{'type': 'text', 'text': 'Unauthorized'}],
            }), 401
        return None

    def mcp_rpc():
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32700, 'message': 'Parse error'},
            }), 400

        headers = {
            'mcp-protocol-version': request.headers.get('MCP-Protocol-Version', ''),
            'mcp-method': request.headers.get('Mcp-Method', ''),
            'mcp-name': request.headers.get('Mcp-Name', ''),
        }
        response = handle_jsonrpc(payload, registry, context=context, headers=headers)
        if response is None:
            return ('', 202)
        return jsonify(response), 200

    # Match both /mcp and /mcp/. MCP clients typically omit the trailing slash
    # and must not be 308-redirected (POST body would be dropped).
    bp.add_url_rule('/', endpoint='rpc', view_func=mcp_rpc,
                    methods=['POST'], strict_slashes=False)
    bp.add_url_rule('', endpoint='rpc_root', view_func=mcp_rpc,
                    methods=['POST'], strict_slashes=False)

    @bp.route('/manifest', methods=['GET'])
    def manifest():
        tools = registry.list()
        return jsonify({
            'protocol': 'mcp-tools',
            'protocolVersion': '0.1',
            'serverName': 'natural20-webapp',
            'description': ('Tool surface over the running Natural20 game; '
                            'lets a DM-acting LLM inspect, mutate and drive entities.'),
            'toolCount': len(tools),
            'categories': sorted({t['category'] for t in tools}),
        })

    @bp.route('/tools/list', methods=['GET'])
    def tools_list():
        return jsonify({'tools': registry.list()})

    @bp.route('/tools/call', methods=['POST'])
    def tools_call():
        payload = request.get_json(silent=True) or {}
        name = payload.get('name')
        arguments = payload.get('arguments') or {}
        if not name:
            return jsonify({
                'isError': True,
                'content': [{'type': 'text', 'text': 'Missing "name" in request body'}],
            }), 400
        envelope = registry.call(name, arguments, context=context)
        status = 200 if not envelope.get('isError') else 400
        return jsonify(envelope), status

    app.register_blueprint(bp)
    # Stash on the app so tests can introspect.
    app.extensions = getattr(app, 'extensions', {}) or {}
    app.extensions['mcp_registry'] = registry
    app.extensions['mcp_context'] = context
    return bp
