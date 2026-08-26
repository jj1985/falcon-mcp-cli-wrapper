"""falcon-mcp-cli: command-line wrapper around the CrowdStrike Falcon MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("falcon-mcp-cli")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0.dev0"
