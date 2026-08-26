# falcon-mcp-cli-wrapper

CLI wrapper (`falcon-cli`) around the CrowdStrike falcon-mcp tool catalog. Usage instructions for agents:

@AGENTS.md

## Working on this repo

- Layout: `falcon_mcp_cli/core.py` (in-process wrapper around the `falcon-mcp` package: catalog + execution), `falcon_mcp_cli/cli.py` (argparse surface), `tests/` (fully offline — stub Falcon client, no credentials needed).
- Run checks before committing: `pytest` and `ruff check falcon_mcp_cli tests`.
- All Falcon logic lives upstream in the `falcon-mcp` dependency; this repo must not reimplement API calls. New upstream tools appear automatically via the registry — if upstream changes its internal APIs (`falcon_mcp.registry`, module `register_tools`/`register_resources`, `FalconClient`), adapt `core.py`.
