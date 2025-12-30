"""Configuration management for MCP Environment Proxy."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """Configuration for an MCP server type."""

    command: str
    args: list[str] = Field(default_factory=list)


class ContextConfig(BaseModel):
    """Configuration for a named context."""

    server: str
    env: dict[str, str] = Field(default_factory=dict)
    description: str | None = None


class ProxyConfig(BaseModel):
    """Root configuration for the proxy."""

    defaults: dict[str, str] = Field(default_factory=dict)
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
    contexts: dict[str, ContextConfig] = Field(default_factory=dict)
    current_context: str | None = None

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "ProxyConfig":
        """Load configuration from YAML file.

        Args:
            config_path: Path to config file. If None, searches in:
                1. MCP_ENV_PROXY_CONFIG environment variable
                2. ./contexts.yaml
                3. ~/.config/mcp-env-proxy/contexts.yaml

        Returns:
            ProxyConfig instance
        """
        if config_path is None:
            config_path = os.environ.get("MCP_ENV_PROXY_CONFIG")

        if config_path is None:
            # Try local first
            local_config = Path("contexts.yaml")
            if local_config.exists():
                config_path = local_config
            else:
                # Try user config dir
                user_config = Path.home() / ".config" / "mcp-env-proxy" / "contexts.yaml"
                if user_config.exists():
                    config_path = user_config

        if config_path is None:
            # Return empty config if no file found
            return cls()

        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls.model_validate(data)

    def get_context(self, name: str) -> ContextConfig | None:
        """Get a context by name."""
        return self.contexts.get(name)

    def get_server(self, name: str) -> ServerConfig | None:
        """Get a server config by name."""
        return self.servers.get(name)

    def build_env(self, context_name: str) -> dict[str, str]:
        """Build environment variables for a context.

        Merges defaults with context-specific env vars.
        """
        context = self.get_context(context_name)
        if context is None:
            raise ValueError(f"Context not found: {context_name}")

        # Start with current environment
        env = dict(os.environ)

        # Apply defaults
        env.update(self.defaults)

        # Apply context-specific env
        env.update(context.env)

        return env

    def get_command(self, context_name: str) -> tuple[str, list[str]]:
        """Get command and args for a context.

        Returns:
            Tuple of (command, args)
        """
        context = self.get_context(context_name)
        if context is None:
            raise ValueError(f"Context not found: {context_name}")

        server = self.get_server(context.server)
        if server is None:
            raise ValueError(f"Server not found: {context.server}")

        return server.command, server.args
