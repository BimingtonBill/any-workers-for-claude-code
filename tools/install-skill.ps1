# Install the DeepSeek worker harness as the deepseek-agents and advisor-loop Claude Code skills.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/install-skill.ps1 [-DryRun] [-Target <folder>]
#
# Works from the DeepSeek Workers harness folder and from the shareable kit (tools/build-kit.py makes it),
# which have the same layout: skill/, launcher/, tools/, templates/.
#
# Backs up an installed skill first (to ~/.claude-deepseek/backups, outside ~/.claude/skills so Claude
# Code doesn't load the backup as a second skill), then copies in:
#   SKILL.md                              from skill/deepseek-agents/
#   ds-agent.ps1 and its helpers          from launcher/
#   tools/*                               every tool, so a session can run them from any project folder
#   templates/brief*.md, *-schema.json    brief templates and the result schema ds_impl.ps1 falls back on
# and every other skill in skill/ beside it (advisor-loop, and the /delegation and /spend-limit commands). -Target installs into another folder instead of ~/.claude/skills
# (for testing an install without touching the live one).
#
# It also registers ds_hook.py in ~/.claude/settings.json (backed up first; other settings are kept) as a
# PreToolUse and PostToolUse hook on Bash and PowerShell, so every DeepSeek lead runs in the background with a
# watcher for its workers. -NoHooks skips that; -Target never touches the user's settings.
param([switch]$DryRun, [string]$Target, [switch]$NoHooks)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
# PowerShell names are case-insensitive: $target below overwrites -Target, so note now whether one was given.
$customTarget = [bool]$Target
$skills = if ($Target) { $Target } else { Join-Path $HOME '.claude\skills' }
$target = Join-Path $skills 'deepseek-agents'

$files = [ordered]@{ 'SKILL.md' = 'skill\deepseek-agents\SKILL.md'; 'ds_hook.py' = 'skill\deepseek-agents\ds_hook.py'; 'ds_hook_main.py' = 'skill\deepseek-agents\ds_hook_main.py' }
foreach ($name in 'ds-agent.ps1', 'ds-spawn.ps1', 'ds_mcp.py', 'ds-which.ps1', 'ds-watch.ps1', 'ds-state.ps1', 'ds_common.py', 'ds_spend.py', 'ds_steer.py', 'ds_claude.py', 'ds_envelope.py', 'ds_providers.py', 'ds_allowance.py', 'ds_prepare.py', 'providers.json') {
    $files[$name] = "launcher\$name"
}
foreach ($tool in Get-ChildItem (Join-Path $root 'tools') -File | Where-Object { $_.Extension -in '.py', '.ps1' -and $_.Name -notin 'install-skill.ps1', 'build-kit.py' }) {
    $files["tools\$($tool.Name)"] = "tools\$($tool.Name)"
}
foreach ($t in Get-ChildItem (Join-Path $root 'templates') -File | Where-Object { $_.Name -like 'brief*.md' -or $_.Name -like '*-schema.json' }) {
    $files["templates\$($t.Name)"] = "templates\$($t.Name)"
}
# The other skills are one SKILL.md each: advisor-loop, and the /delegation and /spend-limit commands.
$extras = @(Get-ChildItem (Join-Path $root 'skill') -Directory |
    Where-Object { $_.Name -ne 'deepseek-agents' -and (Test-Path (Join-Path $_.FullName 'SKILL.md')) } |
    ForEach-Object { @{ name = $_.Name; src = "skill\$($_.Name)\SKILL.md"; dst = (Join-Path $skills "$($_.Name)\SKILL.md") } })
$backups = Join-Path $HOME '.claude-deepseek\backups'

if ($DryRun) {
    "target: $target"
    foreach ($k in $files.Keys) { "  $k  <-  $($files[$k])" }
    foreach ($x in $extras) { "  $($x.dst)  <-  $($x.src)" }
    exit 0
}
New-Item -ItemType Directory -Force -Path $backups | Out-Null
if (Test-Path $target) {
    $backup = Join-Path $backups "deepseek-agents.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -Recurse $target $backup
    "backed up the installed skill to $backup"
}
foreach ($k in $files.Keys) {
    $to = Join-Path $target $k
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item -LiteralPath (Join-Path $root $files[$k]) -Destination $to -Force
}
foreach ($x in $extras) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $x.dst) | Out-Null
    if (Test-Path $x.dst) { Copy-Item $x.dst (Join-Path $backups "$($x.name).SKILL.bak-$(Get-Date -Format yyyyMMdd-HHmmss).md") }
    Copy-Item -LiteralPath (Join-Path $root $x.src) -Destination $x.dst -Force
}
"installed $($files.Count) files into $target, and the skills $(($extras | ForEach-Object { $_.name }) -join ', ')"

# --- The lead hook in the user's Claude Code settings ---
if (-not $NoHooks -and -not $customTarget) {
    $python = @('python', 'py') | ForEach-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
    if (-not $python) { 'hook not registered: Python was not found'; exit 0 }
    & $python.Name (Join-Path $target 'ds_hook.py') --install
}
