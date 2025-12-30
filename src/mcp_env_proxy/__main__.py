"""Entry point for mcp-env-proxy."""

import argparse
import logging
import sys
from pathlib import Path

from .config import ProxyConfig
from .server import create_server


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="MCP Environment Proxy - Dynamic environment switching for MCP servers"
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to contexts.yaml config file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="Set log level (default: WARNING)",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else getattr(logging, args.log_level)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    # Load configuration
    try:
        config = ProxyConfig.load(args.config)
    except FileNotFoundError as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    # Create and run server
    server = create_server(config)
    server.run()


if __name__ == "__main__":
    main()
