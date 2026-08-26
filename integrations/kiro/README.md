# Using falcon-cli with Kiro

Two integration points, depending on how you use Kiro:

## Kiro CLI (custom agent)

`falcon-agent.json` defines a [Kiro CLI custom agent](https://kiro.dev/docs/cli/custom-agents/) named `falcon` that knows the falcon-cli workflow (discover → describe → read FQL guide → call) and has sensible permissions:

- **Pre-approved (no confirmation prompt):** the credential-free catalog commands (`tools`, `describe`, `guides`, `guide`, `modules`, `version`) and `check`.
- **Requires your approval at runtime:** every `falcon-cli call` — so nothing touches your Falcon tenant without you seeing the exact command, and state-changing tools additionally carry `read_only: false` / `destructive: true` flags the agent is instructed to respect.

Install it with the repo's install script (`./install.sh --kiro`), or manually:

```bash
# User-wide (all projects)
mkdir -p ~/.kiro/agents
cp integrations/kiro/falcon-agent.json ~/.kiro/agents/falcon.json

# Or per-project
mkdir -p .kiro/agents
cp integrations/kiro/falcon-agent.json .kiro/agents/falcon.json
```

Then use it:

```bash
kiro-cli agent list                 # confirm "falcon" appears
kiro-cli chat --agent falcon        # start a session with the agent
# or make it the default:
kiro-cli agent set-default falcon
kiro-cli chat
```

Make sure `FALCON_CLIENT_ID` and `FALCON_CLIENT_SECRET` are exported in the shell where you run `kiro-cli` (or present in a `.env` file in the working directory).

## Kiro IDE (steering file)

For the Kiro IDE, add a steering document so any Kiro session in your workspace knows the tool exists:

```bash
mkdir -p .kiro/steering
cp AGENTS.md .kiro/steering/falcon-cli.md
```

The repo's [AGENTS.md](../../AGENTS.md) is written to work as a steering file as-is.

## Notes

- Kiro CLI evolved from the Amazon Q Developer CLI; if you're on an older `q`-based install, the same JSON works from `~/.aws/amazonq/cli-agents/falcon.json`.
- The agent definition never grants `fs_write` — falcon-cli needs no file writes.
- If your Kiro version rejects an unknown field in the JSON, remove `toolsSettings` (you'll simply be prompted for the catalog commands too).
