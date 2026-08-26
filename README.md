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

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.ps1 | iex
```

Both scripts pick the best available installer (uv → pipx → `pip install --user`), verify the install, and — when [Kiro CLI](https://kiro.dev/docs/cli/) is detected — also set up the `falcon` Kiro agent. Options (`--no-kiro`/`-NoKiro`, `--kiro`/`-Kiro`, `--ref`/`-Ref`, `--uninstall`/`-Uninstall`) work the same in both; for the PowerShell options, download first (`irm …/install.ps1 -OutFile install.ps1`) then run `./install.ps1 -Kiro` etc. If neither Python 3.11+ nor uv is present on Windows, install uv first: `winget install astral-sh.uv`.

Everything is cross-platform: on Windows, login profiles are stored under `%APPDATA%\falcon-cli\` and all commands work the same in PowerShell or cmd.

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

### Sign in with your browser (recommended)

```console
$ falcon-cli login
```

Like the CrowdStrike Foundry CLI's `foundry login`, this starts a local callback server and opens your browser to a sign-in form. The form deep-links to the Falcon console's **API clients and keys** page for your region — create (or reuse) an API client there, paste its ID and secret, and submit. The credentials are **validated live against the Falcon API before anything is saved**, then stored as a named profile in `~/.config/falcon-cli/credentials.json` (file mode `0600`).

- `falcon-cli login --region eu-1` — pick your CrowdStrike region (`us-1`, `us-2`, `eu-1`, `us-gov-1`).
- `falcon-cli login --profile prod --set-default` — store multiple tenants as named profiles; switch per-command with `falcon-cli --profile prod call …` or `FALCON_CLI_PROFILE=prod`.
- `falcon-cli login --manual` — terminal-only prompts for headless machines (secret read without echo).
- `falcon-cli login --no-browser` — prints the local URL instead of auto-opening a browser (SSH port-forwarding setups).
- `falcon-cli profiles` — list stored profiles (secrets never displayed); `falcon-cli logout [NAME|--all]` removes them.

The local form is loopback-only, single-use (a random token gates every request), and shuts down as soon as credentials are saved or after 10 minutes.

> **Why paste credentials at all?** Foundry's login can end-to-end provision an API client because the Falcon console has a dedicated authorize flow for it. The public Falcon API offers no equivalent for third-party tools — API clients must be created in the console — so `falcon-cli login` automates everything around that step: opening the right console page, validating the pasted credentials, and storing them safely.

### Environment variables

Environment variables override stored profiles (except when a profile is explicitly selected with `--profile`/`FALCON_CLI_PROFILE`, which wins). A `.env` file in the working directory also works (real environment variables win over `.env`).

| Variable | Required | Purpose |
|---|---|---|
| `FALCON_CLIENT_ID` | if not logged in | Falcon API client ID |
| `FALCON_CLIENT_SECRET` | if not logged in | Falcon API client secret |
| `FALCON_BASE_URL` | no | API base URL for your region (default `https://api.crowdstrike.com`) |
| `FALCON_MEMBER_CID` | no | Child CID for Flight Control (MSSP) parent tenants |
| `FALCON_PROXY_URL` | no | HTTPS proxy for outbound Falcon API calls |
| `FALCON_CLI_PROFILE` | no | Name of a stored login profile to use |
| `FALCON_CLI_READ_ONLY` | no | `true` refuses any tool that modifies tenant state |

`FALCON_BASE_URL` by region:

| Region | Base URL |
|---|---|
| US-1 (default) | `https://api.crowdstrike.com` |
| US-2 | `https://api.us-2.crowdstrike.com` |
| EU-1 | `https://api.eu-1.crowdstrike.com` |
| US-GOV-1 | `https://api.laggar.gcw.crowdstrike.com` |

### Creating a Falcon API client

`falcon-cli login` walks you through this in the browser. Doing it by hand:

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
  "member_cid": null,
  "credential_source": "profile:default"
}
```

## Usage

### Discover

These need **no credentials** — the catalog is read from the installed `falcon-mcp` package:

```console
$ falcon-cli modules                      # the capability modules (27 upstream + 2 extras)
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
| `falcon-cli login [--region R] [--profile NAME] [--manual] [--no-browser] [--set-default]` | creates them | Browser-based sign-in; stores a validated credential profile |
| `falcon-cli logout [NAME\|--all]` | no | Remove stored profile(s) |
| `falcon-cli profiles [--json]` | no | List stored profiles (secrets never shown) |
| `falcon-cli modules` | no | List capability modules |
| `falcon-cli tools [--module M] [--search K] [--read-only] [--json]` | no | List tools |
| `falcon-cli describe TOOL` | no | Full description + parameter JSON schema |
| `falcon-cli guides [--json]` | no | List FQL filter guides |
| `falcon-cli guide URI` | no | Print one FQL guide |
| `falcon-cli call TOOL [k=v\|k:=json ...] [--input JSON] [--read-only] [--yes] [--compact]` | **yes** | Execute a tool |
| `falcon-cli check` | **yes** | Verify connectivity and credentials |
| `falcon-cli version` | no | Wrapper + upstream package versions |

Global options (before the subcommand): `--base-url`, `--member-cid`, `--proxy`, `--profile`, `--debug`.

## Fusion SOAR & Foundry extras

Upstream's `fusion` module covers searching workflow definitions/executions, reading results, and executing workflows. `falcon-cli` extends this with three **extra modules** (implemented here, in `falcon_mcp_cli/extras/`, following the upstream module pattern) for the rest of the Fusion/Foundry/automation surface:

**`workflows` module — the full workflow lifecycle** (scopes: `Workflow: Read` / `Workflow: Write`):

```console
# Export a workflow to YAML, edit it, import it back as a new workflow
$ falcon-cli call falcon_search_workflow_definitions filter="name.raw:*'*Containment*'"
$ falcon-cli call falcon_export_workflow id=<definition-id> | jq -r .yaml > wf.yaml
$ falcon-cli call falcon_import_workflow yaml="$(cat wf.yaml)" name="Containment v2" validate_only:=true
$ falcon-cli call falcon_import_workflow yaml="$(cat wf.yaml)" name="Containment v2"

# Modify in place (full-model update), enable/disable/cancel, or delete
$ falcon-cli call falcon_update_workflow --input @definition.json
$ falcon-cli call falcon_workflow_definition_action action_name=disable ids:='["<definition-id>"]'
$ falcon-cli call falcon_delete_workflow ids:='["<definition-id>"]'

# System workflow definitions (the templates apps/integrations ship)
$ falcon-cli call falcon_provision_system_workflow --input @template.json
$ falcon-cli call falcon_promote_system_workflow --input @template-v2.json
$ falcon-cli call falcon_deprovision_system_workflow definition_id=<id>

# The building blocks: activities (the "functions" a workflow can call —
# including your deployed Foundry functions) and triggers
$ falcon-cli call falcon_search_workflow_activities filter="name:*'*ticket*'"
$ falcon-cli call falcon_search_workflow_triggers

# Test runs, resume/retry, and human-input approvals
$ falcon-cli call falcon_mock_execute_workflow definition_id=<id> --input '{"payload": {"mocks": {...}}}'
$ falcon-cli call falcon_workflow_execution_action action_name=resume ids:='["<execution-id>"]'
$ falcon-cli call falcon_get_workflow_human_inputs ids:='["<input-id>"]'
$ falcon-cli call falcon_update_workflow_human_input id=<input-id> input=approve
```

**`foundry` module — the Foundry platform APIs** (scopes: `Custom Storage: Read/Write`, `Foundry Platform`, `API Integrations`):

```console
$ falcon-cli call falcon_list_foundry_collections               # custom-storage collections
$ falcon-cli call falcon_search_foundry_objects collection_name=mycol filter="status:'open'"
$ falcon-cli call falcon_put_foundry_object collection_name=mycol object_key=k1 data:='{"a":1}'
$ falcon-cli call falcon_list_foundry_repos                     # LogScale repos/views
$ falcon-cli call falcon_run_foundry_search query='#event_simpleName=*' repo_or_view=search-all start=1h
$ falcon-cli call falcon_list_api_integrations                  # third-party API plugins
$ falcon-cli call falcon_upload_foundry_lookup_file name=blocklist.csv content="$(cat blocklist.csv)"
```

**`rtradmin` module — RTR content library for response automation** (scopes: `Real Time Response Admin: Read/Write`):

```console
# Version the custom scripts and put-files your response workflows invoke
$ falcon-cli call falcon_create_rtr_script name=Isolate description="..." content="$(cat isolate.ps1)" platform=windows permission_type=group
$ falcon-cli call falcon_list_rtr_scripts filter="name:*'*isolate*'"
$ falcon-cli call falcon_create_rtr_put_file name=tool.exe description="..." content="$(cat tool.exe)"
```

These manage the reusable content library; actually running a script or staging a put-file onto a host happens in an RTR *session* (upstream's `rtr` module).

### On building full Fusion apps

These tools cover the API-driven parts of the Fusion/Foundry app lifecycle: **workflow templates** (create/export/import/update/provision/promote/deprovision), the **collections, lookup files, API integrations, and RTR content** an app bundles, and running/testing it all. What has **no public API** — and so still requires CrowdStrike's own `foundry` CLI and the console — is packaging and publishing an app itself: Foundry **function code** (build/deploy), UI extensions/pages, and app manifest/release management. In practice: author and deploy function code and the app shell with the `foundry` CLI, then use `falcon-cli` to build, wire up, version, and operate everything the app orchestrates. When CrowdStrike ships public app-packaging APIs, they slot into `extras/` the same way these did.

The extras follow the same safety model as everything else: `[writes]`/`[DESTRUCTIVE]` flags, the read-only guardrail, and confirmation prompts all apply. If upstream falcon-mcp later ships equivalent tools, the upstream versions win automatically and the extras get retired.

## Kiro integration

This repo ships a [Kiro](https://kiro.dev) integration under [`integrations/kiro/`](integrations/kiro/):

- **Kiro CLI:** a custom agent (`falcon`) that knows the discover → describe → guide → call workflow. Catalog commands run without confirmation prompts; every `falcon-cli call` requires your runtime approval. Installed automatically by `install.sh` when Kiro is detected, then: `kiro-cli chat --agent falcon`.
- **Kiro IDE:** copy `AGENTS.md` to `.kiro/steering/falcon-cli.md` in your workspace to make any Kiro session falcon-cli-aware.

See [integrations/kiro/README.md](integrations/kiro/README.md) for details. (Claude Code and other AGENTS.md-aware agents need no setup — they pick up [AGENTS.md](AGENTS.md)/[CLAUDE.md](CLAUDE.md) automatically.)

## How it relates to upstream falcon-mcp

Nearly all tool implementations, parameter schemas, FQL guides, and error handling come from [`falcon-mcp`](https://github.com/CrowdStrike/falcon-mcp) (MIT licensed, by CrowdStrike), installed as a regular Python dependency. The one exception is the [Fusion/Foundry extras](#fusion-soar--foundry-extras): thin modules built on the same upstream base classes and FalconPy operations, covering capabilities upstream doesn't ship yet (they defer to upstream if it ever claims the same names). Beyond that, this wrapper adds:

- an argparse CLI (`falcon-cli`) exposing the catalog and in-process tool execution,
- credential-free catalog browsing (tools, schemas, guides),
- shell-friendly parameter passing, JSON output, and meaningful exit codes,
- a read-only guardrail and interactive confirmation for write tools.

To pick up new upstream tools: `uv tool upgrade falcon-mcp-cli` (or reinstall), or bump the `falcon-mcp` version in `pyproject.toml`.

If you want the actual MCP server (for MCP-native clients like Claude Desktop), use upstream `falcon-mcp` directly — this wrapper is for shells.

## License

MIT — see [LICENSE](LICENSE). Wraps [CrowdStrike/falcon-mcp](https://github.com/CrowdStrike/falcon-mcp), also MIT.
