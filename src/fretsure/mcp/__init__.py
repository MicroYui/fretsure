"""Fretsure MCP interoperability adapter."""

from fretsure.mcp.server import (
    CAPABILITIES_URI,
    MCP_VERSION,
    FretsureFastMCP,
    create_server,
    mcp,
)

__all__ = [
    "CAPABILITIES_URI",
    "MCP_VERSION",
    "FretsureFastMCP",
    "create_server",
    "mcp",
]
