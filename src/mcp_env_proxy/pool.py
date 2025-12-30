"""Process pool manager for MCP server subprocesses."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

from .config import ProxyConfig

logger = logging.getLogger(__name__)


@dataclass
class ManagedProcess:
    """A managed MCP server process."""

    context_name: str
    session: ClientSession
    tools: list[Tool] = field(default_factory=list)
    _cleanup: Any = None  # Cleanup function from context manager


class ProcessPool:
    """Manages a pool of MCP server processes.

    Maintains connections to multiple MCP servers, one per context.
    Allows switching between contexts without restarting processes.
    """

    def __init__(self, config: ProxyConfig, max_processes: int = 5):
        """Initialize the process pool.

        Args:
            config: Proxy configuration
            max_processes: Maximum number of concurrent processes
        """
        self.config = config
        self.max_processes = max_processes
        self._processes: dict[str, ManagedProcess] = {}
        self._current_context: str | None = config.current_context
        self._lock = asyncio.Lock()

    @property
    def current_context(self) -> str | None:
        """Get the current active context."""
        return self._current_context

    @property
    def current_process(self) -> ManagedProcess | None:
        """Get the current active process."""
        if self._current_context is None:
            return None
        return self._processes.get(self._current_context)

    async def get_or_create_process(self, context_name: str) -> ManagedProcess:
        """Get an existing process or create a new one.

        Args:
            context_name: Name of the context

        Returns:
            ManagedProcess instance
        """
        async with self._lock:
            # Return existing process if available
            if context_name in self._processes:
                logger.debug(f"Reusing existing process for context: {context_name}")
                return self._processes[context_name]

            # Evict oldest process if at capacity
            if len(self._processes) >= self.max_processes:
                await self._evict_oldest()

            # Create new process
            logger.info(f"Starting new process for context: {context_name}")
            process = await self._spawn_process(context_name)
            self._processes[context_name] = process
            return process

    async def _spawn_process(self, context_name: str) -> ManagedProcess:
        """Spawn a new MCP server process.

        Args:
            context_name: Name of the context

        Returns:
            ManagedProcess instance
        """
        command, args = self.config.get_command(context_name)
        env = self.config.build_env(context_name)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        # Create the stdio client connection
        read_stream, write_stream = await stdio_client(server_params).__aenter__()

        # Create and initialize the session
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        # Get available tools
        tools_result = await session.list_tools()
        tools = tools_result.tools if tools_result else []

        logger.info(f"Process started for {context_name} with {len(tools)} tools")

        return ManagedProcess(
            context_name=context_name,
            session=session,
            tools=tools,
        )

    async def _evict_oldest(self) -> None:
        """Evict the oldest process from the pool."""
        if not self._processes:
            return

        # Don't evict the current context
        candidates = [
            name for name in self._processes.keys()
            if name != self._current_context
        ]

        if not candidates:
            logger.warning("Cannot evict: only current context in pool")
            return

        # Evict the first candidate (simple FIFO)
        oldest = candidates[0]
        await self._terminate_process(oldest)

    async def _terminate_process(self, context_name: str) -> None:
        """Terminate a process.

        Args:
            context_name: Name of the context
        """
        if context_name not in self._processes:
            return

        process = self._processes.pop(context_name)
        logger.info(f"Terminating process for context: {context_name}")

        try:
            await process.session.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error terminating process: {e}")

    async def switch_context(self, context_name: str) -> ManagedProcess:
        """Switch to a different context.

        Args:
            context_name: Name of the context to switch to

        Returns:
            ManagedProcess for the new context
        """
        if context_name not in self.config.contexts:
            raise ValueError(f"Unknown context: {context_name}")

        process = await self.get_or_create_process(context_name)
        self._current_context = context_name
        logger.info(f"Switched to context: {context_name}")
        return process

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the current context's process.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool result
        """
        process = self.current_process
        if process is None:
            raise RuntimeError("No active context. Use switch_context first.")

        result = await process.session.call_tool(tool_name, arguments)
        return result

    async def list_tools(self) -> list[Tool]:
        """List tools available in the current context.

        Returns:
            List of available tools
        """
        process = self.current_process
        if process is None:
            return []
        return process.tools

    async def close(self) -> None:
        """Close all processes in the pool."""
        async with self._lock:
            for context_name in list(self._processes.keys()):
                await self._terminate_process(context_name)

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
                "loaded": name in self._processes,
            })
        return contexts
