# One-step setup for Any Workers for Claude Code.
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
#
# Run from the unzipped kit. It:
#   1. installs the skills into %USERPROFILE%\.claude\skills (backing up any existing copy);
#   2. checks that Python and Claude Code are present, and says what to install if not;
#   3. if no DeepSeek API key is saved yet, opens a separate PowerShell window that asks for it
#      (typed hidden), checks it with DeepSeek, and saves it for this Windows user only.
# The key never passes through this script, the console that ran it, or a Claude chat.
param(
    # Install somewhere else instead of %USERPROFILE%\.claude\skills (for testing).
    [string]$Target,
    # Don't open the key window even if no key is saved (for testing).
    [switch]$NoKeyWindow
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }

Say ''
Say 'Any Workers for Claude Code: setup' 'Cyan'
Say ''

# 1. The skills.
$install = Join-Path $here 'tools\install-skill.ps1'
if (-not (Test-Path $install)) { Say "Can't find tools\install-skill.ps1 next to this script. Run setup.ps1 from the unzipped folder." 'Red'; exit 1 }
$installArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $install)
if ($Target) { $installArgs += @('-Target', $Target) }
& powershell @installArgs | ForEach-Object { Say "  $_" }
if ($LASTEXITCODE) { Say 'The skill install failed (see above).' 'Red'; exit 1 }
Say '[ok] Skills installed.' 'Green'

# Tools installed after this window was opened aren't on its PATH yet; look at the saved PATH too.
$entries = @($env:Path -split ';' | Where-Object { $_ })
foreach ($scope in 'Machine', 'User') {
    foreach ($e in ([string][Environment]::GetEnvironmentVariable('Path', $scope) -split ';')) {
        if ($e -and -not ($entries -contains $e)) { $entries += [Environment]::ExpandEnvironmentVariables($e) }
    }
}
$env:Path = $entries -join ';'

# 2. Python and Claude Code.
$problems = 0
$python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -First 1
if ($python) { Say "[ok] Python found: $(& $python.Source --version 2>&1)" 'Green' }
else {
    Say '[!!] Python 3 not found. Workers need it to talk to each other. Install it with:' 'Yellow'
    Say '       winget install Python.Python.3.12' 'Yellow'
    $problems++
}
$claude = @(
    (Get-Command claude -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    (Join-Path $HOME '.local\bin\claude.exe')
    (Get-ChildItem "$env:APPDATA\Claude\claude-code\*\claude.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    (Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\claude.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($claude) { Say '[ok] Claude Code found.' 'Green' }
else {
    Say "[!!] Claude Code not found. Install the Claude desktop app (https://claude.ai/download) and open its Code tab once." 'Yellow'
    $problems++
}

# 3. The DeepSeek key.
$key = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
if ($key) {
    Say '[ok] A DeepSeek API key is already saved.' 'Green'
} elseif ($NoKeyWindow) {
    Say '[!!] No DeepSeek API key saved yet (key window skipped).' 'Yellow'
    $problems++
} else {
    Say '[..] No DeepSeek API key saved yet. Opening a separate window to ask for it...' 'Yellow'
    $keyScript = Join-Path $here 'tools\set-deepseek-key.ps1'
    Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$keyScript`"")
    Say '     Type your key into that window. It is hidden as you type and never shown to Claude.' 'Yellow'
}

Say ''
if ($problems) { Say "Setup finished with $problems thing(s) to sort out (above)." 'Yellow' }
else { Say 'Setup finished.' 'Green' }
Say 'Last step: restart Claude (close and reopen the app) so it picks up the new skills.' 'Cyan'
Say ''
