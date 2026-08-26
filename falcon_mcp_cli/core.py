"""In-process wrapper around the falcon-mcp package.

falcon-mcp is designed as an MCP server, but its modules are plain Python
classes that register their tools onto a FastMCP instance. This module drives
that machinery directly, without any MCP transport:

- Catalog operations (listing tools, schemas, and FQL guide resources) register
  every module with ``client=None``. Registration never touches the Falcon API,
  so no credentials are needed — the same trick upstream uses in
  ``registry.get_tool_module_map()``.
- Executing a tool builds an authenticated ``FalconClient``, instantiates only
  the module that owns the tool, and invokes it through FastMCP's in-process
  ``call_tool``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from falcon_mcp.client import FalconClient
from mcp.server.fastmcp import FastMCP


@dataclass
class ToolInfo:
    """Everything the CLI needs to know about one Falcon tool."""

    name: str
    module: str
    description: str
    read_only: bool
    destructive: bool
    input_schema: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "read_only": self.read_only,
            "destructive": self.destructive,
            "description": first_line(self.description),
        }

    def full(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "read_only": self.read_only,
            "destructive": self.destructive,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class GuideInfo:
    """A falcon:// documentation resource (FQL filter guide)."""

    uri: str
    name: str
    description: str


def first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


class Catalog:
    """Credential-free view of every tool and guide falcon-mcp provides."""

    def __init__(self, modules: list[str] | None = None):
        from falcon_mcp_cli.extras import merged_available_modules

        available = merged_available_modules()
        if modules:
            unknown = sorted(set(modules) - set(available))
            if unknown:
                raise UsageError(
                    f"Unknown modules: {', '.join(unknown)}. "
                    f"Available: {', '.join(sorted(available))}"
                )
            selected = {name: available[name] for name in modules}
        else:
            selected = dict(available)

        self.server = FastMCP("falcon-cli-catalog")
        self.tool_to_module: dict[str, str] = {}
        for module_name, module_class in selected.items():
            instance = module_class(None)  # registration makes no API calls
            instance.register_tools(self.server)
            if hasattr(instance, "register_resources"):
                instance.register_resources(self.server)
            for tool_name in instance.tools:
                self.tool_to_module[tool_name] = module_name

    def tools(self) -> list[ToolInfo]:
        infos = []
        for tool in _run(self.server.list_tools()):
            module = self.tool_to_module.get(tool.name)
            if module is None:
                continue  # not a module tool (shouldn't happen in catalog server)
            annotations = tool.annotations
            infos.append(
                ToolInfo(
                    name=tool.name,
                    module=module,
                    description=tool.description or "",
                    read_only=bool(annotations and annotations.readOnlyHint),
                    destructive=bool(annotations and annotations.destructiveHint),
                    input_schema=tool.inputSchema or {},
                )
            )
        return sorted(infos, key=lambda t: (t.module, t.name))

    def tool(self, name: str) -> ToolInfo:
        for info in self.tools():
            if info.name == name:
                return info
        raise UsageError(unknown_tool_message(name, list(self.tool_to_module)))

    def guides(self) -> list[GuideInfo]:
        return sorted(
            (
                GuideInfo(uri=str(r.uri), name=r.name or "", description=r.description or "")
                for r in _run(self.server.list_resources())
            ),
            key=lambda g: g.uri,
        )

    def read_guide(self, uri_or_name: str) -> str:
        guides = self.guides()
        match = next(
            (g for g in guides if g.uri == uri_or_name or g.name == uri_or_name), None
        )
        if match is None:
            known = ", ".join(g.uri for g in guides)
            raise UsageError(f"Unknown guide: {uri_or_name}. Known guides: {known}")
        contents = _run(self.server.read_resource(match.uri))
        return "\n".join(
            c.content if isinstance(c.content, str) else c.content.decode()
            for c in contents
        )


class UsageError(Exception):
    """Bad invocation: unknown tool/module, malformed arguments, missing credentials."""


class ToolExecutionError(Exception):
    """The tool ran but reported an error (API failure, bad filter, missing scope)."""

    def __init__(self, result: Any):
        self.result = result
        super().__init__(json.dumps(result, default=str))


def unknown_tool_message(name: str, known: list[str]) -> str:
    import difflib

    suggestions = difflib.get_close_matches(name, known, n=5, cutoff=0.6)
    substring = [t for t in known if name.lower().removeprefix("falcon_") in t.lower()]
    combined = list(dict.fromkeys(suggestions + substring))[:5]
    hint = f" Did you mean: {', '.join(combined)}?" if combined else ""
    return (
        f"Unknown tool: {name}.{hint} "
        "Run `falcon-cli tools` to list every available tool."
    )


def build_client(
    base_url: str | None = None,
    member_cid: str | None = None,
    proxy: str | None = None,
    debug: bool = False,
    user_agent_comment: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> FalconClient:
    """Create and authenticate a Falcon API client.

    ``client_id``/``client_secret`` override the FALCON_CLIENT_ID /
    FALCON_CLIENT_SECRET environment variables (used for stored login profiles).

    Raises:
        UsageError: credentials are missing entirely.
        AuthFailure: credentials are present but authentication failed.
    """
    comment = f"falcon-mcp-cli{'; ' + user_agent_comment if user_agent_comment else ''}"
    try:
        client = FalconClient(
            base_url=base_url,
            debug=debug,
            user_agent_comment=comment,
            client_id=client_id,
            client_secret=client_secret,
            member_cid=member_cid,
            proxy=proxy,
        )
    except ValueError as exc:  # missing FALCON_CLIENT_ID / FALCON_CLIENT_SECRET
        raise UsageError(
            f"{exc} Alternatively, run `falcon-cli login` to sign in via your "
            "browser and store a credential profile."
        ) from exc

    if not client.authenticate():
        raise AuthFailure(client.auth_failure_message())
    return client


class AuthFailure(Exception):
    """Authentication against the Falcon API failed (bad credentials, wrong region)."""


def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    client: FalconClient,
) -> Any:
    """Run one Falcon tool in-process and return its (parsed) result.

    Only the module that owns ``tool_name`` is instantiated, mirroring what the
    MCP server would execute for a tools/call request.
    """
    from falcon_mcp_cli.extras import merged_available_modules, merged_tool_module_map

    tool_map = merged_tool_module_map()
    module_name = tool_map.get(tool_name)
    if module_name is None:
        raise UsageError(unknown_tool_message(tool_name, list(tool_map)))

    server = FastMCP("falcon-cli-exec")
    module_class = merged_available_modules()[module_name]
    instance = module_class(client)
    instance.register_tools(server)
    if hasattr(instance, "register_resources"):
        instance.register_resources(server)

    raw = _run(server.call_tool(tool_name, arguments))
    result = _normalize_result(raw)

    if _is_error(result):
        raise ToolExecutionError(result)
    return result


def _normalize_result(raw: Any) -> Any:
    """Collapse FastMCP content blocks back into plain JSON-friendly data."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (list, tuple)):
        parts = []
        for block in raw:
            text = getattr(block, "text", None)
            if text is None:
                parts.append(str(block))
                continue
            try:
                parts.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                parts.append(text)
        return parts[0] if len(parts) == 1 else parts
    return raw


def _is_error(result: Any) -> bool:
    """Detect falcon-mcp's error convention: dicts carrying an ``error`` key.

    Errors surface either at the top level or one level down inside a
    list-valued key (e.g. ``{"results": [{"error": ...}]}``). This is a
    heuristic — it decides the process exit code, while the full payload is
    printed either way.
    """
    if isinstance(result, dict):
        if "error" in result:
            return True
        return any(
            isinstance(value, list) and _has_error_item(value)
            for value in result.values()
        )
    if isinstance(result, list):
        return _has_error_item(result)
    return False


def _has_error_item(items: list[Any]) -> bool:
    return any(isinstance(item, dict) and "error" in item for item in items)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)
