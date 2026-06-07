# Ping per-device setup. Run ONCE on each machine you deploy Ping to:
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1
# It is idempotent - safe to re-run. It does NOT touch any other machine.
#
# What it does on THIS device:
#   1. Finds Python and installs the dependencies.
#   2. Creates .env from the template if missing.
#   3. Creates a "Ping" shortcut on the Desktop that launches Ping.bat with the
#      project as its working directory (so the icon works from anywhere).
#   4. Reports the detected Claude CLI.
# DPI / click-coordinate awareness is applied by the bot itself at every launch
# (and verified by its startup self-check), so there is nothing device-specific
# to persist for that - it configures itself on whatever machine it runs on.

$ErrorActionPreference = 'Stop'
$proj = $PSScriptRoot
Set-Location $proj
Write-Host "== Ping setup ==  project: $proj"

# 1. Python -----------------------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) {
    Write-Host "Python not found. Install Python 3.10+ (add it to PATH) and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "Python:  $py"

Write-Host "Installing dependencies..."
& $py -m pip install -q -r (Join-Path $proj 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed." -ForegroundColor Red; exit 1 }

# 2. .env -------------------------------------------------------------------
$envFile = Join-Path $proj '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $proj '.env.example') $envFile
    Write-Host ".env created from template - add DISCORD_TOKEN and ALLOWED_USER_IDS." -ForegroundColor Yellow
} else {
    Write-Host ".env already present (left as-is)."
}

# 3. Desktop shortcut -> Ping.bat (working dir = project) --------------------
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Ping.lnk'
$bat = Join-Path $proj 'Ping.bat'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $proj
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,0"
$sc.Description = "Launch the Ping bot"
$sc.Save()
Write-Host "Desktop shortcut: $lnk"

# 4. Claude CLI detection (informational) -----------------------------------
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claude) {
    foreach ($c in @(
        (Join-Path $env:USERPROFILE '.local\bin\claude.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\claude\claude.exe'),
        (Join-Path $env:LOCALAPPDATA 'AnthropicClaude\claude.exe'))) {
        if (Test-Path $c) { $claude = $c; break }
    }
}
if ($claude) { Write-Host "Claude CLI: $claude" }
else { Write-Host "Claude CLI not found - install it or set CLAUDE_BIN in .env." -ForegroundColor Yellow }

Write-Host ""
Write-Host "Setup complete. Double-click 'Ping' on your Desktop to launch." -ForegroundColor Green
