<#
.SYNOPSIS
    Install falcon-cli (CLI wrapper around CrowdStrike falcon-mcp) on Windows,
    and optionally set up the Kiro CLI "falcon" agent.

.DESCRIPTION
    Picks the best available installer (uv, then pipx, then pip --user),
    installs falcon-mcp-cli from GitHub, verifies the install, and installs the
    Kiro agent definition when Kiro is detected.

.EXAMPLE
    # One-liner (defaults):
    irm https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.ps1 | iex

.EXAMPLE
    # With options — download first, then run:
    irm https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper/main/install.ps1 -OutFile install.ps1
    ./install.ps1 -Kiro -Ref main

.PARAMETER Kiro
    Install the Kiro CLI agent (default: auto — installed when kiro-cli or ~\.kiro is present).
.PARAMETER NoKiro
    Skip Kiro setup even if Kiro is detected.
.PARAMETER Ref
    Git branch/tag/commit to install (default: main).
.PARAMETER Source
    Override the pip requirement entirely (e.g. a local path).
.PARAMETER Uninstall
    Remove falcon-cli and the Kiro agent.
#>
[CmdletBinding()]
param(
    [switch]$Kiro,
    [switch]$NoKiro,
    [string]$Ref = "main",
    [string]$Source = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/jj1985/falcon-mcp-cli-wrapper"
$RawUrl = "https://raw.githubusercontent.com/jj1985/falcon-mcp-cli-wrapper"
$KiroAgentFile = Join-Path $HOME ".kiro/agents/falcon.json"

function Write-Info($msg) { Write-Host "==> $msg" -ForegroundColor Blue }
function Write-Warn($msg) { Write-Host "warning: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if ($Uninstall) {
    if ((Test-Cmd "uv") -and ((& uv tool list 2>$null) -match "^falcon-mcp-cli")) {
        Write-Info "Removing falcon-mcp-cli (uv tool)"
        uv tool uninstall falcon-mcp-cli
    }
    elseif ((Test-Cmd "pipx") -and ((& pipx list 2>$null) -match "falcon-mcp-cli")) {
        Write-Info "Removing falcon-mcp-cli (pipx)"
        pipx uninstall falcon-mcp-cli
    }
    elseif (Test-Cmd "pip") {
        $shown = & pip show falcon-mcp-cli 2>$null
        if ($shown) {
            Write-Info "Removing falcon-mcp-cli (pip)"
            pip uninstall -y falcon-mcp-cli
        }
        else {
            Write-Warn "falcon-mcp-cli does not appear to be installed"
        }
    }
    else {
        Write-Warn "falcon-mcp-cli does not appear to be installed"
    }
    if (Test-Path $KiroAgentFile) {
        Write-Info "Removing Kiro agent $KiroAgentFile"
        Remove-Item -Force $KiroAgentFile
    }
    Write-Info "Uninstall complete."
    exit 0
}

if (-not $Source) {
    $Source = "git+$RepoUrl.git@$Ref"
}

# --- 1. Sanity-check Python ---------------------------------------------------
# uv can provision its own Python, so only hard-fail when uv is also absent.
$pythonOk = $false
foreach ($py in @("py", "python3", "python")) {
    if (Test-Cmd $py) {
        & $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonOk = $true; break }
    }
}
if (-not $pythonOk -and -not (Test-Cmd "uv")) {
    Fail ("Python 3.11+ is required (or install uv, which can provision it):`n" +
        "  winget install astral-sh.uv    (or: irm https://astral.sh/uv/install.ps1 | iex)")
}

# --- 2. Install falcon-cli ----------------------------------------------------
if (Test-Cmd "uv") {
    Write-Info "Installing falcon-cli with uv from $Source"
    uv tool install --force --python 3.11 $Source
    if ($LASTEXITCODE -ne 0) { Fail "uv tool install failed" }
}
elseif (Test-Cmd "pipx") {
    Write-Info "Installing falcon-cli with pipx from $Source"
    pipx install --force $Source
    if ($LASTEXITCODE -ne 0) { Fail "pipx install failed" }
}
else {
    Write-Warn "Neither uv nor pipx found; falling back to 'pip install --user' (less isolated)"
    python -m pip install --user --upgrade $Source
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
}

# --- 3. Verify ----------------------------------------------------------------
# Verify via the resolved executable path: a just-installed tool is often not on
# the current session's PATH yet.
$falconCli = (Get-Command "falcon-cli" -ErrorAction SilentlyContinue).Source
if (-not $falconCli) {
    $binHint = Join-Path $HOME ".local/bin"
    foreach ($candidate in @("falcon-cli.exe", "falcon-cli")) {
        $exe = Join-Path $binHint $candidate
        if (Test-Path $exe) { $falconCli = $exe; break }
    }
    if ($falconCli) {
        $sep = [System.IO.Path]::PathSeparator
        Write-Warn "falcon-cli installed to $binHint, which is not on your PATH."
        Write-Warn "Add it for this session with:  `$env:Path = `"$binHint$sep`" + `$env:Path"
        Write-Warn "Or permanently with:  uv tool update-shell   (then open a new terminal)"
        $env:Path = "$binHint$sep" + $env:Path
    }
    else {
        Fail "falcon-cli was installed but is not on PATH; check the installer output above."
    }
}
$version = (& $falconCli version | Out-String).Trim() -replace "\s+", " "
Write-Info "Installed: $version"

# --- 4. Kiro CLI agent (optional) ---------------------------------------------
$installKiro = $Kiro
if (-not $Kiro -and -not $NoKiro) {
    $installKiro = (Test-Cmd "kiro-cli") -or (Test-Path (Join-Path $HOME ".kiro"))
}
if ($NoKiro) { $installKiro = $false }

if ($installKiro) {
    $agentDir = Split-Path $KiroAgentFile -Parent
    New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
    if (Test-Path $KiroAgentFile) {
        Copy-Item $KiroAgentFile "$KiroAgentFile.bak" -Force
        Write-Warn "Existing Kiro agent backed up to $KiroAgentFile.bak"
    }
    $agentSrc = $null
    if ($PSScriptRoot) {
        $candidate = Join-Path $PSScriptRoot "integrations/kiro/falcon-agent.json"
        if (Test-Path $candidate) { $agentSrc = $candidate }
    }
    if ($agentSrc) {
        Copy-Item $agentSrc $KiroAgentFile -Force
    }
    else {
        # Piped install (irm | iex): fetch the agent definition from the repo.
        Write-Info "Fetching Kiro agent definition from $RawUrl/$Ref"
        Invoke-WebRequest -Uri "$RawUrl/$Ref/integrations/kiro/falcon-agent.json" -OutFile $KiroAgentFile
    }
    Write-Info "Kiro CLI agent installed: $KiroAgentFile"
    Write-Info "Use it with:  kiro-cli chat --agent falcon"
}

# --- 5. Next steps ------------------------------------------------------------
Write-Host @"

falcon-cli is ready. Next steps:

  1. Sign in (opens your browser, stores a validated credential profile):
         falcon-cli login
     Or set credentials manually for this session:
         `$env:FALCON_CLIENT_ID = "..."
         `$env:FALCON_CLIENT_SECRET = "..."
     Non-US-1 regions: use 'falcon-cli login --region ...' or FALCON_BASE_URL.

  2. Verify:            falcon-cli check
  3. Explore (no credentials needed):
         falcon-cli tools
         falcon-cli describe falcon_search_hosts
"@
