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

from falcon_mcp_cli import __version__, auth
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


def resolve_credentials(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    """Pick the credential source: explicit profile > environment > default profile.

    Returns (credentials, source) where credentials may be empty (letting
    FalconClient fall back to FALCON_CLIENT_ID/FALCON_CLIENT_SECRET) and source
    describes what was used ("environment", "profile:NAME", or None).
    """
    explicit = getattr(args, "profile", None) or os.environ.get("FALCON_CLI_PROFILE")
    if explicit:
        found = auth.get_profile(explicit)
        if found is None:
            raise UsageError(
                f"No stored profile named {explicit!r}. Run `falcon-cli login "
                f"--profile {explicit}` to create it, or `falcon-cli profiles` to list them."
            )
        name, profile = found
        return dict(profile), f"profile:{name}"

    if os.environ.get("FALCON_CLIENT_ID") and os.environ.get("FALCON_CLIENT_SECRET"):
        return {}, "environment"

    found = auth.get_profile(None)
    if found:
        name, profile = found
        return dict(profile), f"profile:{name}"
    return {}, None


def _make_client(args: argparse.Namespace):
    creds, source = resolve_credentials(args)
    client = build_client(
        base_url=args.base_url or creds.get("base_url"),
        member_cid=args.member_cid or creds.get("member_cid"),
        proxy=args.proxy,
        debug=args.debug,
        client_id=creds.get("client_id"),
        client_secret=creds.get("client_secret"),
    )
    return client, source


# --- commands ---------------------------------------------------------------


def cmd_modules(args: argparse.Namespace) -> int:
    from falcon_mcp_cli.extras import merged_available_modules

    names = sorted(merged_available_modules())
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

    client, _source = _make_client(args)
    result = execute_tool(info.name, arguments, client)
    _print_json(result, compact=args.compact)
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    client, source = _make_client(args)  # raises AuthFailure if credentials are bad
    _print_json(
        {
            "connected": True,
            "base_url": client.base_url,
            "member_cid": client.member_cid,
            "credential_source": source,
        }
    )
    return EXIT_OK


def _mask(client_id: str) -> str:
    return client_id[:6] + "…" if len(client_id) > 6 else "…"


def cmd_login(args: argparse.Namespace) -> int:
    profile_name = args.profile or "default"
    validator = auth.make_falcon_validator()

    if args.manual:
        import getpass

        print(f"Region: {args.region} ({auth.REGIONS[args.region]['api']})")
        print(
            "Create an API client in the Falcon console under Support and resources\n"
            f"-> API clients and keys: {auth.REGIONS[args.region]['console']}/api-clients-and-keys"
        )
        client_id = input("Client ID: ").strip()
        client_secret = getpass.getpass("Client secret: ").strip()
        member_cid = input("Member CID (optional, MSSP only): ").strip() or None
        candidate = {
            "client_id": client_id,
            "client_secret": client_secret,
            "base_url": auth.REGIONS[args.region]["api"],
            "region": args.region,
            "member_cid": member_cid,
        }
        error = validator(candidate)
        if error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_AUTH
        auth.save_profile(profile_name, candidate, make_default=args.set_default)
        result = {"profile": profile_name, **candidate}
    else:
        result = auth.run_login_server(
            validator,
            region=args.region,
            profile=profile_name,
            port=args.port,
            open_browser=not args.no_browser,
            timeout=args.timeout,
        )
        if result is None:
            print(
                "error: login timed out before credentials were submitted. "
                "Re-run `falcon-cli login`, or use `falcon-cli login --manual` "
                "on machines without a browser.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if args.set_default:
            store = auth.load_store()
            store["default_profile"] = result["profile"]
            auth.save_store(store)

    store = auth.load_store()
    _print_json(
        {
            "logged_in": True,
            "profile": result["profile"],
            "client_id": _mask(result["client_id"]),
            "base_url": result["base_url"],
            "member_cid": result.get("member_cid"),
            "default_profile": store.get("default_profile"),
            "stored_at": str(auth.credentials_path()),
        }
    )
    return EXIT_OK


def cmd_logout(args: argparse.Namespace) -> int:
    if args.all:
        path = auth.credentials_path()
        existed = path.exists()
        if existed:
            path.unlink()
        print(f"Removed all stored profiles{'' if existed else ' (none were stored)'}.")
        return EXIT_OK

    store = auth.load_store()
    name = args.name or store.get("default_profile")
    if not name:
        raise UsageError("No stored profiles. Nothing to log out of.")
    if not auth.delete_profile(name):
        raise UsageError(
            f"No stored profile named {name!r}. Run `falcon-cli profiles` to list them."
        )
    print(f"Removed profile {name!r}.")
    return EXIT_OK


def cmd_profiles(args: argparse.Namespace) -> int:
    store = auth.load_store()
    default = store.get("default_profile")
    entries = [
        {
            "name": name,
            "client_id": _mask(profile.get("client_id", "")),
            "base_url": profile.get("base_url"),
            "region": profile.get("region"),
            "member_cid": profile.get("member_cid"),
            "default": name == default,
        }
        for name, profile in sorted(store.get("profiles", {}).items())
    ]
    if args.json:
        _print_json({"profiles": entries, "default_profile": default})
    elif not entries:
        print("No stored profiles. Run `falcon-cli login` to create one.")
    else:
        for e in entries:
            marker = " (default)" if e["default"] else ""
            print(f"{e['name']}{marker}  client_id={e['client_id']}  {e['base_url']}")
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
            "call and check use FALCON_CLIENT_ID/FALCON_CLIENT_SECRET or a profile "
            "stored by `falcon-cli login`."
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
    parser.add_argument(
        "--profile",
        default=None,
        help="Use a stored credential profile from `falcon-cli login` "
        "(env: FALCON_CLI_PROFILE; default: environment variables, then the default profile)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "login",
        help="Sign in via your browser and store a credential profile",
        description=(
            "Foundry-CLI-style login: starts a local callback server, opens your "
            "browser to a form that links to the Falcon console's API clients page, "
            "validates the credentials against the Falcon API, and stores them as a "
            "named profile. Use --manual for a terminal-only flow on headless machines."
        ),
    )
    p.add_argument(
        "--region",
        choices=sorted(auth.REGIONS),
        default=auth.DEFAULT_REGION,
        help=f"CrowdStrike region (default: {auth.DEFAULT_REGION})",
    )
    # SUPPRESS so this doesn't clobber the global --profile when omitted.
    p.add_argument(
        "--profile",
        default=argparse.SUPPRESS,
        help="Name for the stored profile (default: default)",
    )
    p.add_argument("--manual", action="store_true", help="Prompt in the terminal instead of the browser")
    p.add_argument("--no-browser", action="store_true", help="Start the local form but don't auto-open a browser")
    p.add_argument("--port", type=int, default=0, help="Local callback port (default: random)")
    p.add_argument(
        "--timeout",
        type=float,
        default=auth.LOGIN_TIMEOUT_SECONDS,
        help="Seconds to wait for the browser submission (default: 600)",
    )
    p.add_argument(
        "--set-default",
        action="store_true",
        help="Make this profile the default credential source",
    )
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="Remove a stored credential profile")
    p.add_argument("name", nargs="?", help="Profile to remove (default: the default profile)")
    p.add_argument("--all", action="store_true", help="Remove every stored profile")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("profiles", help="List stored credential profiles")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    p.set_defaults(func=cmd_profiles)

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
