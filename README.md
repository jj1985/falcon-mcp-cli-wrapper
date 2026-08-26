# falcon-mcp-cli-wrapper

**`falcon-cli` — the [CrowdStrike Falcon MCP server](https://github.com/CrowdStrike/falcon-mcp), converted into a command-line tool.**

The upstream `falcon-mcp` project exposes 130+ CrowdStrike Falcon operations (hosts, detections, vulnerabilities, threat intel, cloud security, RTR, and more) as MCP tools for AI assistants. This wrapper takes those exact same tools and makes each one callable from a plain shell — no MCP client, no running server, no protocol layer.

```console
$ falcon-cli call falcon_search_hosts filter='platform_name:"Windows"' limit:=5
```

It works by importing `falcon-mcp` as a Python library and driving its module registry in-process, so the tool catalog always matches whatever version of `falcon-mcp` is installed. When upstream adds a tool, `falcon-cli` gets it by upgrading the dependency — no code changes here.

**Who is this for?**

- **Humans** who want Falcon API access from the terminal, scripts, and cron jobs with a discoverable command surface (`tools` → `describe` → `call`) instead of memorizing raw API endpoints.
- **AI agents** (Claude Code, Cursor, or any agent with shell access) that want Falcon capabilities without an MCP server connection. Agents should read **[AGENTS.md](AGENTS.md)** — it contains everything needed to use this tool effectively.

## Installation

Requires Python 3.11+ (or [uv](https://docs.astral.sh/uv/), which provisions one automatically).

**Quick install (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.sh | bash
```

The script picks the best available installer (uv → pipx → `pip install --user`), verifies the install, and — when [Kiro CLI](https://kiro.dev/docs/cli/) is detected — also sets up the `falcon` Kiro agent (skip with `--no-kiro`, force with `--kiro`; `--uninstall` removes everything; `--help` for all options).

**Manual install:**

```bash
# Isolated install with uv or pipx
uv tool install git+https://github.com/jj1985/falcon-mcp-cli-wrapper.git
# or
pipx install git+https://github.com/jj1985/falcon-mcp-cli-wrapper.git

# Or into the current Python environment
pip install git+https://github.com/jj1985/falcon-mcp-cli-wrapper.git
```

For development:

```bash
git clone https://github.com/jj1985/falcon-mcp-cli-wrapper.git
cd falcon-mcp-cli-wrapper
pip install -e ".[dev]"
pytest   # the full test suite runs offline, no credentials needed
```

Verify the install (works without credentials):

```console
$ falcon-cli version
$ falcon-cli tools --module hosts
```

## Configuration

Credentials come from environment variables or a `.env` file in the working directory (real environment variables win over `.env`).

| Variable | Required | Purpose |
|---|---|---|
| `FALCON_CLIENT_ID` | for `call`/`check` | Falcon API client ID |
| `FALCON_CLIENT_SECRET` | for `call`/`check` | Falcon API client secret |
| `FALCON_BASE_URL` | no | API base URL for your region (default `https://api.crowdstrike.com`) |
| `FALCON_MEMBER_CID` | no | Child CID for Flight Control (MSSP) parent tenants |
| `FALCON_PROXY_URL` | no | HTTPS proxy for outbound Falcon API calls |
| `FALCON_CLI_READ_ONLY` | no | `true` refuses any tool that modifies tenant state |

`FALCON_BASE_URL` by region:

| Region | Base URL |
|---|---|
| US-1 (default) | `https://api.crowdstrike.com` |
| US-2 | `https://api.us-2.crowdstrike.com` |
| EU-1 | `https://api.eu-1.crowdstrike.com` |
| US-GOV-1 | `https://api.laggar.gcw.crowdstrike.com` |

### Creating a Falcon API client

1. In the Falcon console, go to **Support and resources** → **API clients and keys**.
2. Create an API client and grant it the scopes for the capabilities you plan to use (e.g. `Hosts: Read`, `Alerts: Read`, `Vulnerabilities: Read`). The upstream project documents [the scopes each module needs](https://github.com/CrowdStrike/falcon-mcp#api-scopes).
3. Copy the client ID and secret into `FALCON_CLIENT_ID` / `FALCON_CLIENT_SECRET`.

You don't have to get scopes right up front: when a call fails with a 403, the error output names the exact scopes required (`required_scopes`) and how to fix it.

Check connectivity and credentials any time:

```console
$ falcon-cli check
{
  "connected": true,
  "base_url": "https://api.crowdstrike.com",
  "member_cid": null
}
```

## Usage

### Discover

These need **no credentials** — the catalog is read from the installed `falcon-mcp` package:

```console
$ falcon-cli modules                      # the 27 capability modules
$ falcon-cli tools                        # all tools, grouped by module
$ falcon-cli tools --module hosts         # one module
$ falcon-cli tools --search vulnerab      # keyword search over names/descriptions
$ falcon-cli tools --read-only            # only tools that cannot modify anything
$ falcon-cli tools --json                 # machine-readable
$ falcon-cli describe falcon_search_hosts # full description + JSON parameter schema
```

Write-capable tools are flagged `[writes]`, irreversible ones `[DESTRUCTIVE]`.

### Compose FQL filters

Most search tools take a `filter` parameter in Falcon Query Language. Each one ships a guide documenting exactly which fields and operators that endpoint accepts:

```console
$ falcon-cli guides                                   # list all 49 guides
$ falcon-cli guide falcon://hosts/search/fql-guide    # print one
```

An unsupported field in a filter returns an *empty result*, not an error — read the guide before composing a filter.

### Call tools

```console
# String parameters: key=value      JSON-typed parameters: key:=value
$ falcon-cli call falcon_search_hosts filter='platform_name:"Windows"' limit:=5

# Full JSON input (inline, from a file, or from stdin)
$ falcon-cli call falcon_search_detections --input '{"filter": "status:'\''new'\''", "limit": 10}'
$ falcon-cli call falcon_search_detections --input @params.json
$ echo '{"limit": 10}' | falcon-cli call falcon_search_detections --input -
```

Results print as pretty JSON on stdout (`--compact` for one line). Pipe to `jq` for post-processing:

```console
$ falcon-cli call falcon_search_hosts limit:=100 --compact | jq -r '.results[].hostname'
```

### Example workflows

```console
# Triage: newest high-severity detections
$ falcon-cli guide falcon://detections/search/fql-guide
$ falcon-cli call falcon_search_detections filter="severity_name:'High'" limit:=10 sort="created_timestamp|desc"

# Vulnerabilities: critical, with remediation info
$ falcon-cli call falcon_search_vulnerabilities filter="cve.severity:'CRITICAL'" limit:=10

# Threat intel: look up an actor
$ falcon-cli call falcon_search_actors filter="name:*'*BEAR*'"

# How many Windows hosts haven't been seen in a week?
$ falcon-cli call falcon_search_hosts filter="platform_name:'Windows'+last_seen:<'now-7d'" limit:=1 | jq '.pagination.total'
```

## Safety

- **Read-only mode.** `falcon-cli call --read-only …` or `FALCON_CLI_READ_ONLY=true` refuses any tool that modifies tenant state. Recommended for AI-agent and unattended use unless writes are intended.
- **Interactive confirmation.** When run in a terminal, state-changing tools prompt before executing (`--yes`/`-y` skips the prompt). Non-interactive invocations (scripts, agents) don't prompt — use read-only mode as the guardrail there.
- **Least privilege.** The API client's scopes are the hard boundary: a tool without a granted scope fails with a 403 that names the missing scope.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | The tool executed but reported an error (API error, bad filter, missing scope) — details are in the JSON output |
| 2 | Usage or configuration error (unknown tool/module, malformed arguments, missing credentials) |
| 3 | Falcon API authentication failed (bad credentials, wrong region) |

## Command reference

| Command | Credentials | Description |
|---|---|---|
| `falcon-cli modules` | no | List capability modules |
| `falcon-cli tools [--module M] [--search K] [--read-only] [--json]` | no | List tools |
| `falcon-cli describe TOOL` | no | Full description + parameter JSON schema |
| `falcon-cli guides [--json]` | no | List FQL filter guides |
| `falcon-cli guide URI` | no | Print one FQL guide |
| `falcon-cli call TOOL [k=v\|k:=json ...] [--input JSON] [--read-only] [--yes] [--compact]` | **yes** | Execute a tool |
| `falcon-cli check` | **yes** | Verify connectivity and credentials |
| `falcon-cli version` | no | Wrapper + upstream package versions |

Global options (before the subcommand): `--base-url`, `--member-cid`, `--proxy`, `--debug`.

## Kiro integration

This repo ships a [Kiro](https://kiro.dev) integration under [`integrations/kiro/`](integrations/kiro/):

- **Kiro CLI:** a custom agent (`falcon`) that knows the discover → describe → guide → call workflow. Catalog commands run without confirmation prompts; every `falcon-cli call` requires your runtime approval. Installed automatically by `install.sh` when Kiro is detected, then: `kiro-cli chat --agent falcon`.
- **Kiro IDE:** copy `AGENTS.md` to `.kiro/steering/falcon-cli.md` in your workspace to make any Kiro session falcon-cli-aware.

See [integrations/kiro/README.md](integrations/kiro/README.md) for details. (Claude Code and other AGENTS.md-aware agents need no setup — they pick up [AGENTS.md](AGENTS.md)/[CLAUDE.md](CLAUDE.md) automatically.)

## How it relates to upstream falcon-mcp

This project contains **no Falcon API logic of its own**. All tool implementations, parameter schemas, FQL guides, and error handling come from [`falcon-mcp`](https://github.com/CrowdStrike/falcon-mcp) (MIT licensed, by CrowdStrike), installed as a regular Python dependency. This wrapper adds:

- an argparse CLI (`falcon-cli`) exposing the catalog and in-process tool execution,
- credential-free catalog browsing (tools, schemas, guides),
- shell-friendly parameter passing, JSON output, and meaningful exit codes,
- a read-only guardrail and interactive confirmation for write tools.

To pick up new upstream tools: `uv tool upgrade falcon-mcp-cli` (or reinstall), or bump the `falcon-mcp` version in `pyproject.toml`.

If you want the actual MCP server (for MCP-native clients like Claude Desktop), use upstream `falcon-mcp` directly — this wrapper is for shells.

## License

MIT — see [LICENSE](LICENSE). Wraps [CrowdStrike/falcon-mcp](https://github.com/CrowdStrike/falcon-mcp), also MIT.
