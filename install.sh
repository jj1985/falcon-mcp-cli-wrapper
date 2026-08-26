#!/usr/bin/env bash
# install.sh — install falcon-cli (CLI wrapper around CrowdStrike falcon-mcp)
# and optionally set up the Kiro CLI "falcon" agent.
#
# Usage:
#   ./install.sh [options]                          # from a clone
#   curl -fsSL https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.sh | bash
#
# Options:
#   --kiro         Install the Kiro CLI agent (default: auto — installed when
#                  kiro-cli or ~/.kiro is present)
#   --no-kiro      Skip Kiro setup even if Kiro is detected
#   --ref REF      Git branch/tag/commit to install (default: main)
#   --source SRC   Override the pip requirement entirely (e.g. a local path)
#   --uninstall    Remove falcon-cli and the Kiro agent
#   -h, --help     Show this help
set -euo pipefail

REPO_URL="https://github.com/jj1985/falcon-mcp-cli-wrapper"
RAW_URL="https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper"
REF="main"
SOURCE=""
KIRO="auto"
UNINSTALL=0

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --kiro) KIRO="yes" ;;
        --no-kiro) KIRO="no" ;;
        --ref) REF="${2:?--ref needs a value}"; shift ;;
        --source) SOURCE="${2:?--source needs a value}"; shift ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '2,15p'; exit 0 ;;
        *) fail "Unknown option: $1 (see --help)" ;;
    esac
    shift
done

KIRO_AGENT_FILE="$HOME/.kiro/agents/falcon.json"

if [ "$UNINSTALL" = 1 ]; then
    if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^falcon-mcp-cli'; then
        info "Removing falcon-mcp-cli (uv tool)"
        uv tool uninstall falcon-mcp-cli
    elif command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q 'falcon-mcp-cli'; then
        info "Removing falcon-mcp-cli (pipx)"
        pipx uninstall falcon-mcp-cli
    elif command -v pip >/dev/null 2>&1 && pip show falcon-mcp-cli >/dev/null 2>&1; then
        info "Removing falcon-mcp-cli (pip)"
        pip uninstall -y falcon-mcp-cli
    else
        warn "falcon-mcp-cli does not appear to be installed"
    fi
    if [ -f "$KIRO_AGENT_FILE" ]; then
        info "Removing Kiro agent $KIRO_AGENT_FILE"
        rm -f "$KIRO_AGENT_FILE"
    fi
    info "Uninstall complete."
    exit 0
fi

[ -n "$SOURCE" ] || SOURCE="git+${REPO_URL}.git@${REF}"

# --- 1. Sanity-check Python -------------------------------------------------
PYTHON_OK=0
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 \
        && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON_OK=1
        break
    fi
done
# uv can provision its own Python, so only hard-fail when uv is also absent.
if [ "$PYTHON_OK" = 0 ] && ! command -v uv >/dev/null 2>&1; then
    fail "Python 3.11+ is required (or install uv, which can provision it): https://docs.astral.sh/uv/"
fi

# --- 2. Install falcon-cli ---------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    info "Installing falcon-cli with uv from ${SOURCE}"
    uv tool install --force --python 3.11 "$SOURCE"
elif command -v pipx >/dev/null 2>&1; then
    info "Installing falcon-cli with pipx from ${SOURCE}"
    pipx install --force "$SOURCE"
else
    warn "Neither uv nor pipx found; falling back to 'pip install --user' (less isolated)"
    python3 -m pip install --user --upgrade "$SOURCE"
fi

# --- 3. Verify ---------------------------------------------------------------
if ! command -v falcon-cli >/dev/null 2>&1; then
    BIN_HINT="$HOME/.local/bin"
    if [ -x "$BIN_HINT/falcon-cli" ]; then
        warn "falcon-cli installed to $BIN_HINT, which is not on your PATH."
        warn "Add it with:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        export PATH="$BIN_HINT:$PATH"
    else
        fail "falcon-cli was installed but is not on PATH; check the installer output above."
    fi
fi
info "Installed: $(falcon-cli version | tr -d '\n ' )"

# --- 4. Kiro CLI agent (optional) --------------------------------------------
if [ "$KIRO" = "auto" ]; then
    if command -v kiro-cli >/dev/null 2>&1 || [ -d "$HOME/.kiro" ]; then
        KIRO="yes"
    else
        KIRO="no"
    fi
fi

if [ "$KIRO" = "yes" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
    AGENT_SRC="$SCRIPT_DIR/integrations/kiro/falcon-agent.json"
    mkdir -p "$(dirname "$KIRO_AGENT_FILE")"
    if [ -f "$KIRO_AGENT_FILE" ]; then
        cp "$KIRO_AGENT_FILE" "$KIRO_AGENT_FILE.bak"
        warn "Existing Kiro agent backed up to $KIRO_AGENT_FILE.bak"
    fi
    if [ -f "$AGENT_SRC" ]; then
        cp "$AGENT_SRC" "$KIRO_AGENT_FILE"
    else
        # Piped install (curl | bash): fetch the agent definition from the repo.
        info "Fetching Kiro agent definition from ${RAW_URL}/${REF}"
        curl -fsSL "${RAW_URL}/${REF}/integrations/kiro/falcon-agent.json" -o "$KIRO_AGENT_FILE" \
            || fail "Could not download the Kiro agent definition"
    fi
    info "Kiro CLI agent installed: $KIRO_AGENT_FILE"
    info "Use it with:  kiro-cli chat --agent falcon"
fi

# --- 5. Next steps ------------------------------------------------------------
cat <<'EOF'

falcon-cli is ready. Next steps:

  1. Sign in (opens your browser, stores a validated credential profile):
         falcon-cli login
     Or export credentials manually:
         export FALCON_CLIENT_ID=...
         export FALCON_CLIENT_SECRET=...
     Non-US-1 regions: use `falcon-cli login --region ...` or FALCON_BASE_URL.

  2. Verify:            falcon-cli check
  3. Explore (no credentials needed):
         falcon-cli tools
         falcon-cli describe falcon_search_hosts
EOF
