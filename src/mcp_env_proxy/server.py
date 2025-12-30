"""FastMCP server for the environment proxy."""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import ProxyConfig
from .pool import ProcessPool

logger = logging.getLogger(__name__)


def create_server(config: ProxyConfig) -> FastMCP:
    """Create the MCP proxy server.

    Args:
        config: Proxy configuration

    Returns:
        FastMCP server instance
    """
    mcp = FastMCP(name="mcp-env-proxy")

    # Initialize the process pool
    pool = ProcessPool(config)

    @mcp.tool()
    async def list_contexts() -> list[dict[str, Any]]:
        """List all available contexts.

        Returns a list of contexts with their configuration,
        showing which is currently active and which have cached tools.
        """
        return pool.list_contexts()

    @mcp.tool()
    async def switch_context(context_name: str) -> dict[str, Any]:
        """Switch to a different context.

        This changes the active MCP server connection to use different
        environment variables (AWS_PROFILE, AWS_REGION, etc.).

        Args:
            context_name: Name of the context to switch to

        Returns:
            Information about the new active context including available tools
        """
        return await pool.switch_context(context_name)

    @mcp.tool()
    async def get_current_context() -> dict[str, Any]:
        """Get information about the current context.

        Returns:
            Current context info including available tools
        """
        ctx_name = pool.current_context
        if ctx_name is None:
            return {"context": None, "message": "No context active"}

        ctx = config.get_context(ctx_name)
        tools = await pool.list_tools()

        return {
            "context": ctx_name,
            "server": ctx.server if ctx else None,
            "env": ctx.env if ctx else {},
            "tools_available": len(tools),
            "tool_names": [t.name for t in tools],
        }

    @mcp.tool()
    async def proxy_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool on the current context's MCP server.

        This forwards the tool call to the underlying MCP server
        (EKS, ECS, CloudWatch, etc.) with the current context's
        environment variables.

        Args:
            tool_name: Name of the tool to call on the proxied server
            arguments: Arguments to pass to the tool

        Returns:
            Result from the proxied tool
        """
        if arguments is None:
            arguments = {}

        result = await pool.call_tool(tool_name, arguments)
        return result

    @mcp.tool()
    async def list_proxied_tools() -> list[dict[str, Any]]:
        """List tools available from the current context's MCP server.

        Returns:
            List of tools with their names and descriptions
        """
        tools = await pool.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    # Store pool reference for cleanup
    mcp._pool = pool

    return mcp
