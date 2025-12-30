"""Process pool manager for MCP server subprocesses."""

import asyncio
import logging
import os
import subprocess
import json
from dataclasses import dataclass, field
from typing import Any

from .config import ProxyConfig

logger = logging.getLogger(__name__)


@dataclass
class ToolInfo:
    """Information about an available tool."""
    name: str
    description: str | None = None
    input_schema: dict | None = None


class ProcessPool:
    """Manages MCP server process invocations.

    For simplicity, spawns a fresh process for each tool call.
    This avoids complex async context management issues.
    """

    def __init__(self, config: ProxyConfig, max_processes: int = 5):
        """Initialize the process pool.

        Args:
            config: Proxy configuration
            max_processes: Maximum number of concurrent processes (unused for now)
        """
        self.config = config
        self.max_processes = max_processes
        self._current_context: str | None = config.current_context
        self._tools_cache: dict[str, list[ToolInfo]] = {}

    @property
    def current_context(self) -> str | None:
        """Get the current active context."""
        return self._current_context

    async def switch_context(self, context_name: str) -> dict[str, Any]:
        """Switch to a different context.

        Args:
            context_name: Name of the context to switch to

        Returns:
            Info about the new context including available tools
        """
        if context_name not in self.config.contexts:
            raise ValueError(f"Unknown context: {context_name}")

        self._current_context = context_name
        logger.info(f"Switched to context: {context_name}")

        # Fetch tools for the new context
        tools = await self._fetch_tools(context_name)
        self._tools_cache[context_name] = tools

        return {
            "context": context_name,
            "tools": [{"name": t.name, "description": t.description} for t in tools],
        }

    async def _fetch_tools(self, context_name: str) -> list[ToolInfo]:
        """Fetch available tools from an MCP server.

        Args:
            context_name: Name of the context

        Returns:
            List of available tools
        """
        # Use cached tools if available
        if context_name in self._tools_cache:
            return self._tools_cache[context_name]

        command, args = self.config.get_command(context_name)
        env = self.config.build_env(context_name)

        # Build the MCP request for listing tools
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }

        try:
            result = await self._call_mcp_server(command, args, env, [
                {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-env-proxy", "version": "0.1.0"}
                }},
                request
            ])

            if result and "tools" in result:
                return [
                    ToolInfo(
                        name=t["name"],
                        description=t.get("description"),
                        input_schema=t.get("inputSchema")
                    )
                    for t in result["tools"]
                ]
        except Exception as e:
            logger.error(f"Failed to fetch tools for {context_name}: {e}")

        return []

    async def _call_mcp_server(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        requests: list[dict]
    ) -> dict | None:
        """Call an MCP server with JSON-RPC requests.

        Args:
            command: Command to run
            args: Command arguments
            env: Environment variables
            requests: List of JSON-RPC requests to send

        Returns:
            Last response result or None
        """
        logger.debug(f"Calling MCP server: {command} {args}")

        proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            # Send requests one by one and read responses
            results = []

            for req in requests:
                input_line = json.dumps(req) + "\n"
                logger.debug(f"Sending: {input_line.strip()}")
                proc.stdin.write(input_line.encode())
                await proc.stdin.drain()

            # Close stdin to signal end of input
            proc.stdin.close()
            await proc.stdin.wait_closed()

            # Read all responses with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("MCP server timed out")

            if stderr:
                logger.debug(f"MCP server stderr: {stderr.decode()[:500]}")

            # Parse responses - look for JSON lines
            last_result = None
            for line in stdout.decode().split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    response = json.loads(line)
                    if "result" in response:
                        last_result = response["result"]
                    elif "error" in response:
                        logger.error(f"MCP error: {response['error']}")
                except json.JSONDecodeError:
                    continue

            return last_result

        except Exception as e:
            proc.kill()
            raise RuntimeError(f"MCP server error: {e}")

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the current context's MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool result
        """
        if self._current_context is None:
            raise RuntimeError("No active context. Use switch_context first.")

        context_name = self._current_context
        command, args = self.config.get_command(context_name)
        env = self.config.build_env(context_name)

        requests = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-env-proxy", "version": "0.1.0"}
            }},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": tool_name,
                "arguments": arguments
            }}
        ]

        result = await self._call_mcp_server(command, args, env, requests)
        return result

    async def list_tools(self) -> list[ToolInfo]:
        """List tools available in the current context.

        Returns:
            List of available tools
        """
        if self._current_context is None:
            return []

        if self._current_context in self._tools_cache:
            return self._tools_cache[self._current_context]

        tools = await self._fetch_tools(self._current_context)
        self._tools_cache[self._current_context] = tools
        return tools

    def list_contexts(self) -> list[dict[str, Any]]:
        """List all available contexts.

        Returns:
            List of context info dicts
        """
        contexts = []
        for name, ctx in self.config.contexts.items():
            server = self.config.get_server(ctx.server)
            contexts.append({
                "name": name,
                "server": ctx.server,
                "command": f"{server.command} {' '.join(server.args)}" if server else "unknown",
                "env": ctx.env,
                "active": name == self._current_context,
                "loaded": name in self._tools_cache,
            })
        return contexts
