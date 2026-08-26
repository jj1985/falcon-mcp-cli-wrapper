"""falcon-cli: command-line interface for CrowdStrike Falcon, wrapping falcon-mcp.

Exit codes:
    0  success
    1  the tool ran but reported an error (API error, bad filter, missing scope)
    2  usage/configuration error (unknown tool, bad arguments, missing credentials)
    3  authentication with the Falcon API failed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from falcon_mcp_cli import __version__
from falcon_mcp_cli.core import (
    AuthFailure,
    Catalog,
    ToolExecutionError,
    UsageError,
    build_client,
    execute_tool,
    first_line,
)

EXIT_OK = 0
EXIT_TOOL_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def parse_tool_arguments(pairs: list[str], input_json: str | None) -> dict[str, Any]:
    """Merge --input JSON with key=value / key:=json pairs (pairs win).

    ``key=value``  passes value as a string.
    ``key:=value`` parses value as JSON (numbers, booleans, lists, objects).
    """
    arguments: dict[str, Any] = {}

    if input_json is not None:
        text = input_json
        if text == "-":
            text = sys.stdin.read()
        elif text.startswith("@"):
            try:
                with open(text[1:], encoding="utf-8") as f:
                    text = f.read()
            except OSError as exc:
                raise UsageError(f"Cannot read --input file: {exc}") from exc
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UsageError(f"--input is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise UsageError("--input must be a JSON object of tool parameters")
        arguments.update(parsed)

    for pair in pairs:
        # ':=' marks a JSON-typed value, but only when it appears before any plain
        # '=' (so a=b:=c still parses as the string "b:=c" under key "a").
        if ":=" in pair and pair.find(":=") <= pair.find("="):
            key, _, raw = pair.partition(":=")
            if not key:
                raise UsageError(f"Malformed argument: {pair!r} (expected key:=json)")
            try:
                arguments[key] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise UsageError(
                    f"Value for {key!r} is not valid JSON: {raw!r} ({exc}). "
                    "Use key=value for plain strings."
                ) from exc
        elif "=" in pair:
            key, _, value = pair.partition("=")
            if not key:
                raise UsageError(f"Malformed argument: {pair!r} (expected key=value)")
            arguments[key] = value
        else:
            raise UsageError(
                f"Malformed argument: {pair!r}. Use key=value (string) or key:=json (typed)."
            )
    return arguments


def _print_json(data: Any, compact: bool = False) -> None:
    if compact:
        print(json.dumps(data, default=str, separators=(",", ":")))
    else:
        print(json.dumps(data, indent=2, default=str))


def _make_client(args: argparse.Namespace):
    return build_client(
        base_url=args.base_url,
        member_cid=args.member_cid,
        proxy=args.proxy,
        debug=args.debug,
    )


# --- commands ---------------------------------------------------------------


def cmd_modules(args: argparse.Namespace) -> int:
    from falcon_mcp import registry

    names = sorted(registry.get_module_names())
    if args.json:
        _print_json({"modules": names, "total": len(names)})
    else:
        print("\n".join(names))
    return EXIT_OK


def cmd_tools(args: argparse.Namespace) -> int:
    catalog = Catalog(modules=args.module or None)
    tools = catalog.tools()

    if args.search:
        needle = args.search.lower()
        tools = [
            t
            for t in tools
            if needle in t.name.lower() or needle in t.description.lower()
        ]
    if args.read_only:
        tools = [t for t in tools if t.read_only]

    if args.json:
        _print_json({"tools": [t.summary() for t in tools], "total": len(tools)})
        return EXIT_OK

    current_module = None
    for t in tools:
        if t.module != current_module:
            current_module = t.module
            print(f"\n[{current_module}]")
        flags = "" if t.read_only else (" [DESTRUCTIVE]" if t.destructive else " [writes]")
        print(f"  {t.name}{flags}")
        summary = first_line(t.description)
        if summary:
            print(f"      {summary}")
    print(f"\n{len(tools)} tools. Use `falcon-cli describe <tool>` for parameters.")
    return EXIT_OK


def cmd_describe(args: argparse.Namespace) -> int:
    catalog = Catalog()
    info = catalog.tool(args.tool)
    _print_json(info.full(), compact=args.compact)
    return EXIT_OK


def cmd_guides(args: argparse.Namespace) -> int:
    catalog = Catalog()
    guides = catalog.guides()
    if args.json:
        _print_json(
            {
                "guides": [
                    {"uri": g.uri, "name": g.name, "description": first_line(g.description)}
                    for g in guides
                ],
                "total": len(guides),
            }
        )
    else:
        for g in guides:
            print(g.uri)
    return EXIT_OK


def cmd_guide(args: argparse.Namespace) -> int:
    catalog = Catalog()
    print(catalog.read_guide(args.uri))
    return EXIT_OK


def cmd_call(args: argparse.Namespace) -> int:
    catalog = Catalog()
    info = catalog.tool(args.tool)  # raises UsageError with suggestions if unknown

    read_only_mode = args.read_only or _env_flag("FALCON_CLI_READ_ONLY")
    if read_only_mode and not info.read_only:
        raise UsageError(
            f"Refused: {info.name} modifies tenant state and read-only mode is active "
            "(--read-only flag or FALCON_CLI_READ_ONLY=true). Unset it to allow write tools."
        )

    arguments = parse_tool_arguments(args.params, args.input)

    if not info.read_only and not args.yes and sys.stdin.isatty() and sys.stdout.isatty():
        kind = "DESTRUCTIVE (cannot be undone)" if info.destructive else "state-changing"
        answer = input(
            f"{info.name} is a {kind} tool. Run it with "
            f"{json.dumps(arguments, default=str)}? [y/N] "
        )
        if answer.strip().lower() not in {"y", "yes"}:
            print("Aborted.", file=sys.stderr)
            return EXIT_USAGE

    client = _make_client(args)
    result = execute_tool(info.name, arguments, client)
    _print_json(result, compact=args.compact)
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    client = _make_client(args)  # raises AuthFailure if credentials are bad
    _print_json(
        {
            "connected": True,
            "base_url": client.base_url,
            "member_cid": client.member_cid,
        }
    )
    return EXIT_OK


def cmd_version(args: argparse.Namespace) -> int:
    from importlib.metadata import PackageNotFoundError, version

    def pkg(name: str) -> str:
        try:
            return version(name)
        except PackageNotFoundError:
            return "unknown"

    _print_json(
        {
            "falcon-mcp-cli": __version__,
            "falcon-mcp": pkg("falcon-mcp"),
            "crowdstrike-falconpy": pkg("crowdstrike-falconpy"),
        }
    )
    return EXIT_OK


# --- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="falcon-cli",
        description=(
            "Command-line access to CrowdStrike Falcon via the falcon-mcp tool catalog. "
            "Catalog commands (tools, describe, guides, modules) need no credentials; "
            "call and check require FALCON_CLIENT_ID and FALCON_CLIENT_SECRET."
        ),
        epilog=(
            "Exit codes: 0 success, 1 tool reported an error, 2 usage/config error, "
            "3 Falcon authentication failed."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("FALCON_BASE_URL"),
        help="Falcon API base URL for your region (env: FALCON_BASE_URL, "
        "default: https://api.crowdstrike.com)",
    )
    parser.add_argument(
        "--member-cid",
        default=os.environ.get("FALCON_MEMBER_CID"),
        help="Child CID for Flight Control / MSSP tenants (env: FALCON_MEMBER_CID)",
    )
    parser.add_argument(
        "--proxy",
        default=os.environ.get("FALCON_PROXY_URL"),
        help="HTTPS proxy URL for outbound Falcon API calls (env: FALCON_PROXY_URL)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("modules", help="List falcon-mcp modules (no credentials needed)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("tools", help="List available tools (no credentials needed)")
    p.add_argument(
        "--module",
        "-m",
        action="append",
        metavar="MODULE",
        help="Restrict to one or more modules (repeatable)",
    )
    p.add_argument("--search", "-s", metavar="KEYWORD", help="Filter by keyword in name/description")
    p.add_argument("--read-only", action="store_true", help="Show only read-only tools")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser(
        "describe", help="Show a tool's full description and JSON parameter schema"
    )
    p.add_argument("tool", help="Tool name, e.g. falcon_search_hosts")
    p.add_argument("--compact", action="store_true", help="Single-line JSON output")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("guides", help="List FQL filter-syntax guides (no credentials needed)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    p.set_defaults(func=cmd_guides)

    p = sub.add_parser("guide", help="Print one FQL guide (pass a falcon:// URI)")
    p.add_argument("uri", help="Guide URI from `falcon-cli guides`, e.g. falcon://hosts/fql-guide")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser(
        "call",
        help="Execute a Falcon tool (requires credentials)",
        description=(
            "Execute one Falcon tool. Parameters are given as key=value pairs "
            "(strings) or key:=value pairs (parsed as JSON: numbers, booleans, "
            "lists, objects), and/or a full JSON object via --input. "
            "Example: falcon-cli call falcon_search_hosts "
            "filter='platform_name:\"Windows\"' limit:=5"
        ),
    )
    p.add_argument("tool", help="Tool name, e.g. falcon_search_hosts")
    p.add_argument(
        "params",
        nargs="*",
        metavar="key=value|key:=json",
        help="Tool parameters",
    )
    p.add_argument(
        "--input",
        "-i",
        metavar="JSON|@file|-",
        help="Tool parameters as a JSON object (inline, @file, or - for stdin)",
    )
    p.add_argument(
        "--read-only",
        action="store_true",
        help="Refuse tools that modify tenant state (env: FALCON_CLI_READ_ONLY=true)",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation for state-changing tools",
    )
    p.add_argument("--compact", action="store_true", help="Single-line JSON output")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser("check", help="Verify Falcon API connectivity and credentials")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("version", help="Show wrapper and upstream package versions")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> None:
    # Same behavior as the upstream falcon-mcp server: pick up FALCON_* settings
    # from a .env file in the working directory, without overriding real env vars.
    from dotenv import load_dotenv

    load_dotenv()

    args = build_parser().parse_args(argv)
    try:
        sys.exit(args.func(args))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except AuthFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_AUTH)
    except ToolExecutionError as exc:
        _print_json(exc.result)
        sys.exit(EXIT_TOOL_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
