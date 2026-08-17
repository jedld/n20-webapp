"""JSON-RPC MCP protocol adapter over :class:`ToolRegistry`.

Speaks Streamable HTTP semantics without the official ASGI SDK:

- Protocol ``2026-07-28`` (stateless): ``server/discover``, header mirroring.
- Protocol ``2025-03-26`` / ``2025-11-25``: ``initialize`` handshake fallback.

Tool handlers stay on the existing registry; this module only translates
JSON-RPC methods to registry calls and maps envelopes onto spec-compliant
``tools/call`` results (text content + ``structuredContent``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from .tool_registry import ToolRegistry

PROTOCOL_2026 = '2026-07-28'
PROTOCOL_2025_11 = '2025-11-25'
PROTOCOL_2025_03 = '2025-03-26'
SUPPORTED_VERSIONS = (PROTOCOL_2026, PROTOCOL_2025_11, PROTOCOL_2025_03)

SERVER_NAME = 'natural20-webapp'
SERVER_VERSION = '1.0.0'
SERVER_INSTRUCTIONS = (
    'DM-privileged tools over a running Natural20 VTT instance. '
    'Inspect the world with world.* tools, mutate state with dm.*, '
    'and drive combat with actions.*. All calls run with Dungeon Master privilege.'
)

# JSON-RPC / MCP error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020

TOOLS_LIST_TTL_MS = 30_000
TOOLS_LIST_CACHE_SCOPE = 'instance'

_META_PROTOCOL_VERSION = 'io.modelcontextprotocol/protocolVersion'
_META_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'

_NAMED_METHODS = frozenset({'tools/call', 'prompts/get', 'resources/read'})


def handle_jsonrpc(
    message: Any,
    registry: ToolRegistry,
    context: Any = None,
    headers: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC message.

    Returns a JSON-RPC response object, or ``None`` for notifications
    (caller should answer HTTP 202 with an empty body).
    """
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, 'Invalid Request: expected a JSON object')

    msg_id = message.get('id', _MISSING)
    is_notification = msg_id is _MISSING
    jsonrpc = message.get('jsonrpc')
    method = message.get('method')
    params = message.get('params') if isinstance(message.get('params'), dict) else {}

    if jsonrpc != '2.0' or not isinstance(method, str) or not method:
        if is_notification:
            return None
        return _error(
            None if msg_id is _MISSING else msg_id,
            INVALID_REQUEST,
            'Invalid Request: jsonrpc 2.0 and method are required',
        )

    header_error = _check_headers(method, params, headers)
    if header_error is not None:
        if is_notification:
            return None
        code, text = header_error
        return _error(msg_id, code, text)

    try:
        result = _dispatch(method, params, registry, context)
    except _RpcError as exc:
        if is_notification:
            return None
        return _error(msg_id, exc.code, exc.message, data=exc.data)
    except Exception as exc:  # noqa: BLE001 - surface as JSON-RPC internal error
        if is_notification:
            return None
        return _error(msg_id, INTERNAL_ERROR, f'{exc.__class__.__name__}: {exc}')

    if is_notification:
        return None
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def call_result_from_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Map a registry envelope onto an MCP ``tools/call`` result."""
    raw_content = envelope.get('content') or []
    is_error = bool(envelope.get('isError'))
    out_content = []
    structured: Any = None
    for item in raw_content:
        if not isinstance(item, dict):
            out_content.append({'type': 'text', 'text': str(item)})
            continue
        kind = item.get('type')
        if kind == 'json':
            structured = item.get('json')
            out_content.append({'type': 'text', 'text': _dump(structured)})
        elif kind == 'text':
            out_content.append({'type': 'text', 'text': item.get('text') or ''})
        else:
            out_content.append(item)
    if not out_content:
        out_content = [{'type': 'text', 'text': ''}]
    result: Dict[str, Any] = {'content': out_content, 'isError': is_error}
    if structured is not None and not is_error:
        result['structuredContent'] = structured
    return result


def protocol_tool_list(registry: ToolRegistry) -> Dict[str, Any]:
    """``tools/list`` result: spec descriptors, stable order, cache hints."""
    tools = []
    for manifest in registry.list():
        tools.append({
            'name': manifest['name'],
            'description': manifest.get('description') or '',
            'inputSchema': manifest.get('inputSchema') or {
                'type': 'object',
                'properties': {},
            },
        })
    return {
        'tools': tools,
        'ttlMs': TOOLS_LIST_TTL_MS,
        'cacheScope': TOOLS_LIST_CACHE_SCOPE,
    }


def discover_result() -> Dict[str, Any]:
    return {
        'supportedVersions': list(SUPPORTED_VERSIONS),
        'capabilities': _server_capabilities(),
        '_meta': {
            _META_SERVER_INFO: {
                'name': SERVER_NAME,
                'version': SERVER_VERSION,
            },
        },
    }


def initialize_result(params: Mapping[str, Any]) -> Dict[str, Any]:
    requested = params.get('protocolVersion')
    if requested in SUPPORTED_VERSIONS:
        version = requested
    else:
        version = PROTOCOL_2025_11
    return {
        'protocolVersion': version,
        'capabilities': _server_capabilities(),
        'serverInfo': {
            'name': SERVER_NAME,
            'version': SERVER_VERSION,
        },
        'instructions': SERVER_INSTRUCTIONS,
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

_MISSING = object()


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _dispatch(method: str, params: Dict[str, Any],
              registry: ToolRegistry, context: Any) -> Any:
    if method == 'ping':
        return {}
    if method == 'initialize':
        return initialize_result(params)
    if method == 'server/discover':
        return discover_result()
    if method == 'notifications/initialized':
        return None
    if method == 'notifications/cancelled':
        return None
    if method == 'tools/list':
        return protocol_tool_list(registry)
    if method == 'tools/call':
        return _tools_call(params, registry, context)
    if method == 'resources/list':
        return {'resources': []}
    if method == 'resources/templates/list':
        return {'resourceTemplates': []}
    if method == 'prompts/list':
        return {'prompts': []}
    raise _RpcError(METHOD_NOT_FOUND, f'Method not found: {method}')


def _tools_call(params: Dict[str, Any], registry: ToolRegistry, context: Any) -> Dict[str, Any]:
    name = params.get('name')
    if not isinstance(name, str) or not name.strip():
        raise _RpcError(INVALID_PARAMS, 'tools/call requires params.name')
    arguments = params.get('arguments') or {}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _RpcError(INVALID_PARAMS, 'tools/call arguments must be an object')
    envelope = registry.call(name, arguments, context=context)
    return call_result_from_envelope(envelope)


def _server_capabilities() -> Dict[str, Any]:
    return {
        'tools': {'listChanged': False},
    }


def _check_headers(
    method: str,
    params: Mapping[str, Any],
    headers: Optional[Mapping[str, str]],
) -> Optional[tuple]:
    """Return ``(code, message)`` when 2026 header mirroring fails."""
    normalized = _normalize_headers(headers)
    version = _request_protocol_version(params, normalized)
    header_method = normalized.get('mcp-method')
    header_name = normalized.get('mcp-name')
    header_version = normalized.get('mcp-protocol-version')

    if header_version and header_version not in SUPPORTED_VERSIONS:
        return (
            INVALID_PARAMS,
            f'Unsupported protocol version: {header_version}',
        )

    if header_method and header_method != method:
        return (
            HEADER_MISMATCH,
            f'Header mismatch: Mcp-Method ({header_method!r}) != method ({method!r})',
        )

    if method in _NAMED_METHODS:
        expected_name = params.get('name') if isinstance(params.get('name'), str) else ''
        if header_name and expected_name and header_name != expected_name:
            return (
                HEADER_MISMATCH,
                f'Header mismatch: Mcp-Name ({header_name!r}) != params.name ({expected_name!r})',
            )

    if version == PROTOCOL_2026:
        if not header_method:
            return (HEADER_MISMATCH, 'Missing Mcp-Method header')
        if method in _NAMED_METHODS and not header_name:
            return (HEADER_MISMATCH, 'Missing Mcp-Name header')
        if header_version and header_version != PROTOCOL_2026:
            return (
                HEADER_MISMATCH,
                f'Header mismatch: MCP-Protocol-Version ({header_version!r}) '
                f'!= {PROTOCOL_2026}',
            )
    return None


def _request_protocol_version(
    params: Mapping[str, Any],
    headers: Mapping[str, str],
) -> Optional[str]:
    header_version = headers.get('mcp-protocol-version')
    if header_version:
        return header_version
    meta = params.get('_meta')
    if isinstance(meta, dict):
        meta_version = meta.get(_META_PROTOCOL_VERSION)
        if isinstance(meta_version, str) and meta_version:
            return meta_version
    requested = params.get('protocolVersion')
    if isinstance(requested, str) and requested:
        return requested
    return None


def _normalize_headers(headers: Optional[Mapping[str, str]]) -> Dict[str, str]:
    if not headers:
        return {}
    out: Dict[str, str] = {}
    for key, value in headers.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out[str(key).lower()] = text
    return out


def _error(msg_id: Any, code: int, message: str,
           data: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {'code': code, 'message': message}
    if data is not None:
        error['data'] = data
    return {'jsonrpc': '2.0', 'id': None if msg_id is _MISSING else msg_id,
            'error': error}


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, default=str, ensure_ascii=False)
    except TypeError:
        return str(value)
