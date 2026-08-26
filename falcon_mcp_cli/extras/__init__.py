"""Extra tool modules that extend the upstream falcon-mcp catalog.

Upstream falcon-mcp deliberately ships a curated tool surface; some Falcon API
capabilities (full Fusion SOAR workflow lifecycle, Foundry collections/search)
have no upstream tool yet. Each module here follows the exact upstream pattern
(a ``BaseModule`` subclass registering tools onto FastMCP), calls the Falcon API
exclusively through ``FalconClient.command`` with FalconPy operation IDs, and is
merged into the catalog at a *lower* precedence than upstream:

- A module name that upstream later claims wins over the extra of the same name.
- When upstream grows equivalent tools, the extra module should be retired.

This is the one sanctioned exception to "all Falcon logic lives upstream": thin
operation wrappers for capabilities upstream does not cover, nothing more.
"""

from __future__ import annotations

from falcon_mcp.modules.base import BaseModule


def get_extra_modules() -> dict[str, type[BaseModule]]:
    """Extra modules by name. Imported lazily to keep CLI startup cheap."""
    from falcon_mcp_cli.extras.foundry import FoundryModule
    from falcon_mcp_cli.extras.workflows import WorkflowsModule

    return {
        "workflows": WorkflowsModule,
        "foundry": FoundryModule,
    }


def merged_available_modules() -> dict[str, type[BaseModule]]:
    """Upstream modules plus extras, with upstream winning any name clash."""
    from falcon_mcp import registry

    merged = dict(registry.get_available_modules())
    for name, cls in get_extra_modules().items():
        merged.setdefault(name, cls)
    return merged


def merged_tool_module_map() -> dict[str, str]:
    """Upstream tool->module map plus extras, upstream winning any clash."""
    from falcon_mcp import registry
    from mcp.server.fastmcp import FastMCP

    mapping = dict(registry.get_tool_module_map())
    upstream_modules = set(registry.get_available_modules())
    scratch = FastMCP("falcon-cli-extras-probe")
    for name, cls in get_extra_modules().items():
        if name in upstream_modules:
            continue
        instance = cls(None)  # registration makes no API calls
        instance.register_tools(scratch)
        for tool_name in instance.tools:
            mapping.setdefault(tool_name, name)
    return mapping
