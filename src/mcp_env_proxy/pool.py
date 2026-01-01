"""Process pool manager for MCP server subprocesses."""

import asyncio
import logging
import json
from dataclasses import dataclass
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

    Uses interactive communication with MCP servers, reading responses
    line by line as they arrive.
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

        try:
            # Initialize first
            init_request = {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-env-proxy", "version": "0.1.0"}
                }
            }

            tools_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }

            result = await self._call_mcp_interactive(
                command, args, env,
                [init_request, tools_request],
                expected_responses=2
            )

            # Look for tools/list response (id=1)
            for resp in result:
                if resp.get("id") == 1 and "result" in resp:
                    tools_data = resp["result"].get("tools", [])
                    return [
                        ToolInfo(
                            name=t["name"],
                            description=t.get("description"),
                            input_schema=t.get("inputSchema")
                        )
                        for t in tools_data
                    ]

        except Exception as e:
            logger.error(f"Failed to fetch tools for {context_name}: {e}")

        return []

    async def _call_mcp_interactive(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        requests: list[dict],
        expected_responses: int = 1,
        timeout: float = 30.0
    ) -> list[dict]:
        """Call an MCP server interactively, reading responses as they arrive.

        Args:
            command: Command to run
            args: Command arguments
            env: Environment variables
            requests: List of JSON-RPC requests to send
            expected_responses: Number of responses to wait for
            timeout: Overall timeout in seconds

        Returns:
            List of JSON-RPC responses
        """
        logger.debug(f"Calling MCP server: {command} {args}")

        proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        responses = []

        try:
            async def read_responses():
                """Read JSON responses from stdout."""
                while len(responses) < expected_responses:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=timeout
                    )
                    if not line:
                        break

                    line_str = line.decode().strip()
                    if not line_str or not line_str.startswith("{"):
                        continue

                    try:
                        resp = json.loads(line_str)
                        responses.append(resp)
                        logger.debug(f"Received response id={resp.get('id')}")
                    except json.JSONDecodeError:
                        continue

            # Start reading responses in background
            read_task = asyncio.create_task(read_responses())

            # Send all requests
            for req in requests:
                input_line = json.dumps(req) + "\n"
                logger.debug(f"Sending request id={req.get('id')}")
                proc.stdin.write(input_line.encode())
                await proc.stdin.drain()
                # Small delay between requests
                await asyncio.sleep(0.1)

            # Wait for responses with timeout
            try:
                await asyncio.wait_for(read_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for responses, got {len(responses)}/{expected_responses}")

            # Close stdin and terminate
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.stdin.wait_closed(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

            # Kill process
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()

            return responses

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

        init_request = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-env-proxy", "version": "0.1.0"}
            }
        }

        tool_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        responses = await self._call_mcp_interactive(
            command, args, env,
            [init_request, tool_request],
            expected_responses=2,
            timeout=60.0  # Longer timeout for tool calls
        )

        # Look for tools/call response (id=1)
        for resp in responses:
            if resp.get("id") == 1:
                if "result" in resp:
                    return resp["result"]
                elif "error" in resp:
                    raise RuntimeError(f"Tool error: {resp['error']}")

        raise RuntimeError("No response received for tool call")

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
