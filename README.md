# MCP Environment Proxy

A dynamic MCP (Model Context Protocol) proxy that allows switching environment variables on-the-fly. Instead of configuring multiple MCP servers for different AWS accounts/regions, use a single proxy that can switch contexts dynamically.

## Problem

When working with multiple AWS accounts or Kubernetes clusters, you typically need one MCP server per environment:

```json
{
  "mcpServers": {
    "eks-production": { "env": { "AWS_PROFILE": "prod" } },
    "eks-staging": { "env": { "AWS_PROFILE": "staging" } },
    "eks-dev": { "env": { "AWS_PROFILE": "dev" } }
  }
}
```

This consumes context and requires restarting Claude to switch environments.

## Solution

MCP Environment Proxy provides a single MCP server that can switch between contexts on-demand:

```json
{
  "mcpServers": {
    "mcp-env-proxy": {
      "command": "uvx",
      "args": ["mcp-env-proxy@latest", "-c", "~/.config/mcp-env-proxy/contexts.yaml"]
    }
  }
}
```

Then dynamically switch contexts:

```
> switch_context("eks-production")
> list_k8s_resources(...)

> switch_context("eks-staging")
> list_k8s_resources(...)
```

## Installation

```bash
# With uvx (recommended)
uvx mcp-env-proxy@latest

# With pip
pip install mcp-env-proxy

# From source
git clone https://github.com/KamorionLabs/mcp-env-proxy.git
cd mcp-env-proxy
pip install -e .
```

## Configuration

Create a `contexts.yaml` file:

```yaml
# Default environment variables for all contexts
defaults:
  FASTMCP_LOG_LEVEL: ERROR

# Define MCP server types
servers:
  eks:
    command: uvx
    args: ["awslabs.eks-mcp-server@latest"]
  ecs:
    command: uvx
    args: ["awslabs.ecs-mcp-server@latest"]

# Named contexts
contexts:
  production:
    server: eks
    env:
      AWS_PROFILE: WordPress-Production/AWSAdministratorAccess
      AWS_REGION: eu-west-3

  staging:
    server: eks
    env:
      AWS_PROFILE: WordPress-Staging/AWSAdministratorAccess
      AWS_REGION: eu-west-3

  homebox-prod:
    server: ecs
    env:
      AWS_PROFILE: homebox-production/AdministratorAccess
      AWS_REGION: eu-west-3

# Default context on startup
current_context: production
```

Config file locations (in order of precedence):
1. `MCP_ENV_PROXY_CONFIG` environment variable
2. `./contexts.yaml` (current directory)
3. `~/.config/mcp-env-proxy/contexts.yaml`

## Available Tools

### `list_contexts`
List all available contexts with their configuration.

### `switch_context(context_name)`
Switch to a different context. This loads the MCP server with the specified environment variables.

### `get_current_context`
Get information about the currently active context.

### `list_proxied_tools`
List all tools available from the current context's MCP server.

### `proxy_tool(tool_name, arguments)`
Call a tool on the proxied MCP server.

## Process Pool

The proxy maintains a pool of MCP server processes (default: 5). When you switch contexts:
- If the context was previously loaded, it reuses the existing process (fast)
- If it's a new context, it spawns a new process
- If the pool is full, it evicts the oldest unused process

This provides fast context switching while limiting memory usage.

## Usage with Claude Code

Add to your Claude Code MCP configuration:

```bash
claude mcp add mcp-env-proxy -- uvx mcp-env-proxy@latest -c ~/.config/mcp-env-proxy/contexts.yaml
```

Then in conversation:
```
User: Switch to the production EKS cluster
Claude: [calls switch_context("production")]

User: List the pods in the wordpress namespace
Claude: [calls proxy_tool("list_k8s_resources", {"resource_type": "pods", "namespace": "wordpress"})]
```

## Development

```bash
# Clone the repository
git clone https://github.com/KamorionLabs/mcp-env-proxy.git
cd mcp-env-proxy

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Run locally
python -m mcp_env_proxy -c config/contexts.example.yaml -v
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please open an issue or PR on GitHub.
