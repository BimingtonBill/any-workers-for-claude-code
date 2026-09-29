<#
.SYNOPSIS
  Waits on DeepSeek runs so each one can appear as its own entry in the Claude app's Background tasks
  panel, including the workers a DeepSeek lead starts itself.

.DESCRIPTION
  The panel lists only commands the Claude session started, and a lead's workers are started by the
  lead, not by Claude. So Claude starts one of these per run, as a background command:

    -Children <lead>  waits until the lead has started workers not listed in -Known (or the lead has
                      ended), prints each new worker with the command and panel description to watch it,
                      then exits. That exit is Claude's signal. Start it again with the grown -Known list
                      while the lead is still running.
    -Run <run>        waits until that run ends, then prints its state, turns, tokens and report.

  <lead> and <run> are run ids (research-003-x.1) or task ids (research-003-x, meaning its newest
  attempt). Runs are read from the manifest: -StateDir, else the launcher's state-dir rule
  (launcher/ds-state.ps1): DS_STATE_DIR, the current folder's "stateDir", then <current
  folder>\local\agents, then the shared ~\.claude-deepseek\agents. Stopping a watcher does not stop the
  worker; use tools/ds_status.ps1 -Stop <label> for that.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File ds-watch.ps1 -Children lead-001-project-audit
  powershell -NoProfile -ExecutionPolicy Bypass -File ds-watch.ps1 -Run research-001-2-combat.1
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Children,
    [string]$Run,
    # Children already being watched, comma-separated run ids.
    [string]$Known,
    [string]$StateDir,
    [int]$TimeoutMinutes = 240,
    [int]$PollSeconds = 5
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
try { [Console]::OutputEncoding = $utf8 } catch { }
if (-not $Children -and -not $Run) { [Console]::Error.WriteLine('[ds-watch] pass -Children <lead> or -Run <run>'); exit 2 }

# -StateDir is the caller's own override; without one, ask for the launcher's rule for the current folder.
if (-not $StateDir) { $StateDir = & (Join-Path $PSScriptRoot 'ds-state.ps1') -Dir (Get-Location).Path }
$manifest = Join-Path $StateDir 'manifest.jsonl'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$watchStart = Get-Date
$warnedMissing = $false

# Latest record per run id, in start order, among the records that name one of $Ids. Parsing every line took
# about 290 ms per poll on a 1.6 MB manifest, which only grows, so a plain text test comes first: a run's own
# records hold its run id and task id, and a lead's workers hold the lead's run id as their parent_run_id, so
# every record the watcher reads still gets through. Resolve-Run and the kids filter still match exact fields.
function Get-Runs([string[]]$Ids) {
    $runs = [ordered]@{}
    if (-not (Test-Path -LiteralPath $manifest)) { return $runs }
    $Ids = @($Ids | Where-Object { $_ })
    foreach ($line in [IO.File]::ReadAllLines($manifest, $utf8)) {
        if (-not $line.Trim()) { continue }
        if ($Ids) {
            $named = $false
            foreach ($id in $Ids) { if ($line.IndexOf($id, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $named = $true; break } }
            if (-not $named) { continue }
        }
        try { $r = $line | ConvertFrom-Json } catch { continue }
        if ($r.run_id) { $runs[$r.run_id] = $r }
    }
    return $runs
}

# A run id, or a task id meaning its newest attempt.
function Resolve-Run($runs, [string]$Id) {
    if ($runs.Contains($Id)) { return $runs[$Id] }
    $attempts = @($runs.Values | Where-Object { $_.task_id -eq $Id })
    if ($attempts) { return $attempts[-1] }
    return $null
}

function Test-Ended($r) { return $r.state -in @('completed', 'failed', 'timed_out', 'canceled') }

# "research-003-combat-audit" -> kind research, number 003, words "combat audit".
function Split-TaskId([string]$TaskId) {
    if ($TaskId -match '^([a-z]+)-(\d+(?:-\d+)?)-(.+)$') { return @($Matches[1], ($Matches[2] -replace '-', '.'), ($Matches[3] -replace '-', ' ')) }
    if ($TaskId -match '^([a-z]+)-(.+)$') { return @($Matches[1], '', ($Matches[2] -replace '-', ' ')) }
    return @('task', '', $TaskId)
}

function Get-Description($child, [string]$LeadNumber, [int]$Index) {
    $kind, $number, $words = Split-TaskId $child.task_id
    # A lead's worker is numbered under its lead (#008.1), so its entry says which lead it belongs to.
    # write_brief gives every child its own number, and each lead counts from 001, so showing that number
    # alone made two leads' workers both read #001 (the user found this confusing, 2026-09-23). The
    # child's own number, without its leading zeros, is the part after the dot; its order is the fallback.
    if ($LeadNumber) {
        $sub = if ($number -match '^\d+$') { [string][int]$number } else { "$Index" }
        $number = "$LeadNumber.$sub"
    } elseif (-not $number) { $number = "$Index" }
    # A retry is an earlier attempt of the same task under the same parent; a name reused by an
    # unrelated run also raises the attempt number but is not a retry.
    $tags = ''
    $earlier = @($script:allRuns.Values | Where-Object { $_.task_id -eq $child.task_id -and $_.attempt -lt $child.attempt -and $_.parent_run_id -eq $child.parent_run_id })
    if ($earlier) { $tags += " (retry $($earlier.Count + 1))" }
    # Crosstalk is on for every worker by default, so it is not tagged; only its absence is worth noting.
    if ($child.crosstalk -eq $false) { $tags += ' (no crosstalk)' }
    # Shorten the words, never the tags, to stay within about 60 characters.
    $head = "DeepSeek $kind #${number}: "
    $room = 60 - $head.Length - $tags.Length
    if ($words.Length -gt $room) { $words = $words.Substring(0, [Math]::Max(8, $room - 3)).TrimEnd() + '...' }
    return $head + $words + $tags
}

$self = $PSCommandPath -replace '\\', '/'

if ($Children) {
    $knownList = @($Known -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    while ($true) {
        $runs = Get-Runs @($Children)
        $script:allRuns = $runs
        $lead = Resolve-Run $runs $Children
        if (-not $lead) {
            # A manifest that doesn't exist won't grow one by waiting: wrong -StateDir or wrong folder (H5).
            if (-not (Test-Path -LiteralPath $manifest)) { "[ds-watch] no manifest at $manifest; check -StateDir or DS_STATE_DIR (the launcher's state folder)"; exit 1 }
            if ((Get-Date) -gt $deadline) { "[ds-watch] no run $Children appeared in $manifest"; exit 1 }
            if (-not $warnedMissing -and ((Get-Date) - $watchStart).TotalSeconds -gt 120) { "[ds-watch] still no run named $Children in $manifest after 2 minutes; waiting up to $TimeoutMinutes minutes"; $warnedMissing = $true }
            Start-Sleep -Seconds $PollSeconds; continue
        }
        $kids = @($runs.Values | Where-Object { $_.parent_run_id -eq $lead.run_id })
        $new = @($kids | Where-Object { $knownList -notcontains $_.run_id })
        if ($new -or (Test-Ended $lead) -or (Get-Date) -gt $deadline) { break }
        Start-Sleep -Seconds $PollSeconds
    }
    $leadNumber = (Split-TaskId $lead.task_id)[1]
    "[ds-watch] lead $($lead.run_id) is $($lead.state); $($kids.Count) worker(s) so far, $($new.Count) new"
    foreach ($kid in $new) {
        $index = [array]::IndexOf(@($kids | ForEach-Object { $_.run_id }), $kid.run_id) + 1
        ''
        "new worker: $($kid.run_id) ($($kid.kind)) - $($kid.title)"
        "  watch with: powershell -NoProfile -ExecutionPolicy Bypass -File `"$self`" -Run $($kid.run_id) -StateDir `"$StateDir`""
        "  description: $(Get-Description $kid $leadNumber $index)"
    }
    $all = @($kids | ForEach-Object { $_.run_id }) -join ','
    ''
    if (Test-Ended $lead) {
        "[ds-watch] the lead has ended; no need to watch it for more workers."
    } else {
        "[ds-watch] keep watching for more: powershell -NoProfile -ExecutionPolicy Bypass -File `"$self`" -Children $($lead.run_id) -Known $all -StateDir `"$StateDir`""
    }
    exit 0
}

# -Run: wait for one run to end, then show how it went.
while ($true) {
    $r = Resolve-Run (Get-Runs @($Run)) $Run
    if ($r -and (Test-Ended $r)) { break }
    if ((Get-Date) -gt $deadline) {
        "[ds-watch] gave up after $TimeoutMinutes minutes; $Run is $(if ($r) { $r.state } else { 'not in the manifest' }). The worker itself keeps running."
        exit 1
    }
    Start-Sleep -Seconds $PollSeconds
}
$tokens = "$($r.tokens_in) in / $($r.tokens_out) out"
"[ds-watch] $($r.run_id) ($($r.kind)) $($r.state): $($r.turns) turns, $tokens, $($r.seconds) s$(if ($r.error) { ", error: $($r.error)" })"
"title:  $($r.title)"
if ($r.parent_run_id) { "parent: $($r.parent_run_id)" }
"report: $($r.report)"
''
if ($r.report -and (Test-Path -LiteralPath $r.report)) {
    $lines = [IO.File]::ReadAllLines($r.report, $utf8)
    $lines | Select-Object -First 60
    if ($lines.Count -gt 60) { "... ($($lines.Count - 60) more lines in the report file)" }
}
exit 0
