"""Stdio MCP proxy that forwards JSON-RPC lines to a running Natural20 webapp.

Claude Desktop and other hosts that only launch local subprocesses can run::

    python -m webapp.mcp.stdio

Environment:

- ``N20_MCP_URL`` — Streamable HTTP endpoint (default ``http://127.0.0.1:5001/mcp``).
- ``N20_MCP_DM_TOKEN`` — shared secret sent as ``Authorization: Bearer`` and
  ``X-MCP-Token``.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Mapping, Optional

DEFAULT_MCP_URL = 'http://127.0.0.1:5001/mcp'
_META_PROTOCOL_VERSION = 'io.modelcontextprotocol/protocolVersion'


def mcp_url() -> str:
    return (os.environ.get('N20_MCP_URL') or DEFAULT_MCP_URL).strip().rstrip('/')


def build_forward_headers(message: Mapping[str, Any]) -> Dict[str, str]:
    """Mirror JSON-RPC method/name into Streamable HTTP headers."""
    method = message.get('method') if isinstance(message.get('method'), str) else ''
    params = message.get('params') if isinstance(message.get('params'), dict) else {}
    name = params.get('name') if isinstance(params.get('name'), str) else ''
    version = _protocol_version(message, params)

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
    }
    token = os.environ.get('N20_MCP_DM_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
        headers['X-MCP-Token'] = token
    if version:
        headers['MCP-Protocol-Version'] = version
    if method:
        headers['Mcp-Method'] = method
    if name:
        headers['Mcp-Name'] = name
    return headers


def forward_message(
    message: Mapping[str, Any],
    *,
    url: Optional[str] = None,
    post: Optional[Callable[..., Any]] = None,
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """POST one JSON-RPC message to the webapp MCP endpoint.

    Returns the JSON-RPC response object, or ``None`` when the server
    accepted a notification (HTTP 202 / empty body).
    """
    target = url or mcp_url()
    body = json.dumps(message, ensure_ascii=False).encode('utf-8')
    headers = build_forward_headers(message)
    if post is not None:
        status, raw = post(target, body=body, headers=headers, timeout=timeout)
    else:
        status, raw = _http_post(target, body=body, headers=headers, timeout=timeout)

    if status == 202 or not raw:
        if 'id' not in message:
            return None
        if not raw:
            return {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'error': {
                    'code': -32603,
                    'message': f'Empty response from MCP endpoint (HTTP {status})',
                },
            }

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {
            'jsonrpc': '2.0',
            'id': message.get('id'),
            'error': {
                'code': -32603,
                'message': f'Non-JSON response from MCP endpoint (HTTP {status})',
            },
        }
    if isinstance(parsed, dict):
        return parsed
    return {
        'jsonrpc': '2.0',
        'id': message.get('id'),
        'error': {
            'code': -32603,
            'message': 'MCP endpoint returned a non-object JSON body',
        },
    }


def serve_stdio(
    stdin=None,
    stdout=None,
    stderr=None,
    *,
    post: Optional[Callable[..., Any]] = None,
    url: Optional[str] = None,
) -> None:
    """Read newline-delimited JSON-RPC from stdin and write responses to stdout."""
    inn = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    for raw_line in inn:
        line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode('utf-8').strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_json(out, {
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32700, 'message': f'Parse error: {exc}'},
            })
            continue
        if not isinstance(message, dict):
            _write_json(out, {
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32600, 'message': 'Invalid Request: expected a JSON object'},
            })
            continue
        try:
            response = forward_message(message, url=url, post=post)
        except Exception as exc:  # noqa: BLE001 - keep the stdio loop alive
            print(f'n20 mcp stdio: {exc}', file=err)
            if 'id' in message:
                _write_json(out, {
                    'jsonrpc': '2.0',
                    'id': message.get('id'),
                    'error': {'code': -32603, 'message': f'{exc.__class__.__name__}: {exc}'},
                })
            continue
        if response is not None:
            _write_json(out, response)


def main() -> int:
    try:
        serve_stdio()
    except KeyboardInterrupt:
        return 0
    return 0


def _protocol_version(message: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    meta = params.get('_meta')
    if isinstance(meta, dict):
        meta_version = meta.get(_META_PROTOCOL_VERSION)
        if isinstance(meta_version, str) and meta_version.strip():
            return meta_version.strip()
    requested = params.get('protocolVersion')
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    method = message.get('method')
    if method == 'server/discover':
        return '2026-07-28'
    if method == 'initialize':
        return '2025-11-25'
    return '2025-11-25'


def _http_post(url: str, *, body: bytes, headers: Mapping[str, str],
               timeout: float) -> tuple:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            return resp.getcode() or 200, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        return exc.code, raw


def _write_json(out, payload: Mapping[str, Any]) -> None:
    out.write(json.dumps(payload, ensure_ascii=False))
    out.write('\n')
    out.flush()


if __name__ == '__main__':
    sys.exit(main())
