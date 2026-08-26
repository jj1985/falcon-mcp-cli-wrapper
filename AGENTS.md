# Using falcon-cli (for AI agents)

`falcon-cli` gives you CrowdStrike Falcon capabilities (EDR hosts, detections/alerts, vulnerabilities, threat intel, cloud security, identity protection, RTR, and more) as shell commands. It wraps the CrowdStrike `falcon-mcp` tool catalog — the same 130+ tools an MCP server would expose — but you invoke them with ordinary shell calls instead of MCP.

## The loop

1. **Find a tool.** `falcon-cli tools --search <keyword> --json` or `falcon-cli tools --module <module> --json`. Never guess tool names.
2. **Get its parameters.** `falcon-cli describe <tool_name>` returns the full description and JSON schema of the parameters, plus `read_only` / `destructive` flags.
3. **If the tool takes a `filter` parameter, read its FQL guide first.** The tool description names a `falcon://.../fql-guide` resource; print it with `falcon-cli guide <uri>`. This matters because an unsupported filter field returns an **empty result, not an error** — indistinguishable from a genuine no-match. Compose filters only from fields the guide lists.
4. **Call it.** `falcon-cli call <tool_name> key=value key2:=5 …` and parse the JSON from stdout.

Steps 1–3 need no credentials. Step 4 needs credentials from one of: `FALCON_CLIENT_ID` + `FALCON_CLIENT_SECRET` in the environment (or a `.env` file in the working directory), or a profile stored by the human via `falcon-cli login` (list with `falcon-cli profiles`; select with `--profile NAME` or `FALCON_CLI_PROFILE`). `falcon-cli check` reports which source is active as `credential_source`. If no credentials exist, tell the user to run `falcon-cli login` — do not attempt the browser login yourself; it requires a human in the loop.

## Passing parameters

- `key=value` — value passed as a **string**. Quote FQL filters carefully for the shell: `filter='platform_name:"Windows"'`.
- `key:=value` — value parsed as **JSON**: `limit:=100`, `include_details:=true`, `ids:='["a","b"]'`.
- `--input '<json object>'`, `--input @file.json`, or `--input -` (stdin) — all parameters at once; explicit `key=value` pairs override `--input` keys.

## Reading results

- Success → pretty JSON on stdout, exit code `0`. Add `--compact` for single-line JSON. Search tools typically return `{"results": [...], "pagination": {"total": N, "next": ...}}` — use `pagination.total` to answer "how many" questions.
- Tool-level failure → JSON containing an `"error"` key on stdout, exit code `1`. On permission failures the payload includes `required_scopes` and a `resolution` telling you which API scopes the Falcon API client is missing — report that to the user; you cannot fix scopes yourself.
- Your mistake (unknown tool, malformed args, missing credentials) → message on stderr, exit code `2`. Unknown tool names come back with "did you mean" suggestions.
- Authentication failure → message on stderr with a hint (bad credentials vs. wrong region), exit code `3`.

## Safety rules

- `read_only: false` in `describe` output (shown as `[writes]` in `tools` output) means the tool **changes tenant state**; `destructive: true` (`[DESTRUCTIVE]`) means the effect **cannot be undone** (e.g. containing a host, executing a SOAR workflow). Confirm with the user before calling either kind.
- If your task is purely investigative, set `FALCON_CLI_READ_ONLY=true` in the environment (or pass `--read-only` on each call): the CLI will then refuse write tools with exit code `2`.
- There is no interactive confirmation when stdin is not a TTY — the exit-code-2 guardrail above and your own judgment are the protection.

## Quick reference

```bash
falcon-cli modules                          # list capability modules (no creds)
falcon-cli tools --json                     # all tools (no creds)
falcon-cli tools --search detect --json     # keyword search (no creds)
falcon-cli describe falcon_search_hosts     # parameter schema (no creds)
falcon-cli guides                           # list FQL guides (no creds)
falcon-cli guide falcon://hosts/search/fql-guide
falcon-cli check                            # verify credentials work
falcon-cli call falcon_search_hosts filter='hostname:"PC-*"' limit:=10
falcon-cli call falcon_search_detections --input '{"filter":"severity_name:'\''High'\''","limit":5}'
```

Common environment variables: `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET` (needed for `call`/`check` unless a login profile is stored); `FALCON_BASE_URL` (non-US-1 regions); `FALCON_CLI_PROFILE` (select a stored profile); `FALCON_CLI_READ_ONLY=true` (guardrail). Full list in [README.md](README.md#configuration).

## FQL essentials

Falcon Query Language, used by most `filter` parameters. Syntax: `field:[operator]'value'`.

- AND is `+`, OR is `,` — e.g. `platform_name:'Windows'+status:'normal'`.
- Operators: `!` (not), `>`, `>=`, `<`, `<=`, `*` (wildcard match, e.g. `hostname:*'PC*'`).
- Timestamps are UTC ISO-8601; many fields accept relative forms like `'now-7d'`.
- **Field availability differs per endpoint** — always check the tool's `falcon://` guide rather than reusing a field name from another tool.
