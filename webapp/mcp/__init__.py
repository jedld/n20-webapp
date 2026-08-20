"""MCP (Model Context Protocol) surface for the running game.

This package exposes DM-privileged tools over a running Natural20 webapp
so an LLM (in-process or an external host such as Cursor / Claude) can
inspect, mutate, and drive entities.

Logical modules:

- :mod:`webapp.mcp.tools_world` — read-only inspection (maps, entities,
  battle state).
- :mod:`webapp.mcp.tools_dm` — direct DM mutations (HP, statuses,
  properties, spawn/remove/teleport, notebook).
- :mod:`webapp.mcp.tools_actions` — turn-based execution (MoveAction,
  attack, spell, generic action dispatch, end_turn, start/end battle).
- :mod:`webapp.mcp.tools_npc` — NPC spatial awareness (location, areas).

All tools are registered into a shared :class:`ToolRegistry`.
:mod:`webapp.mcp.protocol` translates JSON-RPC MCP methods onto that
registry. The Flask blueprint in :mod:`webapp.mcp.routes` exposes:

- Streamable HTTP JSON-RPC at ``POST /mcp`` (Cursor / Claude Code).
- Legacy REST ``GET /mcp/manifest``, ``GET /mcp/tools/list``,
  ``POST /mcp/tools/call`` for the in-app LLM bridge.

:mod:`webapp.mcp.stdio` is a newline-delimited JSON-RPC proxy for hosts
that only launch local subprocesses (Claude Desktop).
"""

from .context import MCPContext
from .tool_registry import Tool, ToolRegistry, tool
from .routes import register_mcp_blueprint, build_default_registry

__all__ = [
    'MCPContext',
    'Tool',
    'ToolRegistry',
    'tool',
    'register_mcp_blueprint',
    'build_default_registry',
]
