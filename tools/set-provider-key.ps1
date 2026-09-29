# Ask for a model provider's API key (hidden as it is typed), check it with the provider, and save it for this
# Windows user under the variable launcher/providers.json names. Opened in its own window, so the key never
# appears in a Claude chat or in the window that started it.
#   powershell -NoProfile -ExecutionPolicy Bypass -File set-provider-key.ps1 -Provider meta [-Tier contributor]
#   (DeepSeek keeps its own set-deepseek-key.ps1, which also shows the balance.)
param([Parameter(Mandatory = $true)][string]$Provider, [string]$Tier = '')
$here = Split-Path -Parent $PSScriptRoot
$registryPath = @((Join-Path $here 'launcher\providers.json'), (Join-Path $here 'providers.json'), (Join-Path $PSScriptRoot 'providers.json')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $registryPath) { Write-Host 'providers.json was not found beside the launcher.' -ForegroundColor Red; exit 2 }
$registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
$p = $registry.providers.$Provider
if (-not $p) { Write-Host "Unknown provider '$Provider'. Known: $(($registry.providers.PSObject.Properties.Name) -join ', ')" -ForegroundColor Red; exit 2 }
$keyEnv = [string]$p.keyEnv
if ($Tier) {
    $t = $p.tiers.$Tier
    if (-not $t) { Write-Host "$($p.name) has no '$Tier' tier." -ForegroundColor Red; exit 2 }
    if ($t.keyEnv) { $keyEnv = [string]$t.keyEnv }
}
$title = "$($p.name) API key$(if ($Tier) { " ($Tier tier)" })"
$Host.UI.RawUI.WindowTitle = $title
Write-Host ''
Write-Host $title -ForegroundColor Cyan
Write-Host ''
Write-Host "Get one at https://$($p.keyPage) (sign in, create an API key, copy it)."
if ($Tier -and $p.tiers.$Tier.trainsOnPrompts) {
    Write-Host "On the $Tier tier $($p.name) may train on what workers send it. Workers use it only in projects you list" -ForegroundColor Yellow
    Write-Host 'yourself in ~/.claude-deepseek/providers.json, and only on their tracked files.' -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Paste the key below and press Enter. Nothing shows while you paste; that is normal.'
while ($true) {
    $secure = Read-Host $title -AsSecureString
    $key = [Net.NetworkCredential]::new('', $secure).Password.Trim()
    if (-not $key) { Write-Host 'Nothing was entered. Try again, or close this window to stop.' -ForegroundColor Yellow; continue }
    # A minimal request (16 output tokens, the least Meta accepts) tells a rejected key apart from a working one. A tier with
    # several regional addresses (MiMo's Token Plan) is tried at each until one accepts the key.
    $headers = @{ 'anthropic-version' = '2023-06-01' }
    if ($p.auth -eq 'auth_token') { $headers['Authorization'] = "Bearer $key" } else { $headers['x-api-key'] = $key }
    $body = @{ model = ([string]$p.model -replace '\[1m\]$', ''); max_tokens = 16; messages = @(@{ role = 'user'; content = 'hi' }) } | ConvertTo-Json -Depth 4
    $tierInfo = if ($Tier) { $p.tiers.$Tier } else { $null }
    $urls = if ($tierInfo -and $tierInfo.baseUrls) { @($tierInfo.baseUrls) } elseif ($tierInfo -and $tierInfo.baseUrl) { @($tierInfo.baseUrl) } else { @($p.baseUrl) }
    $result = 'unconfirmed'; $workingUrl = $null; $lastError = ''
    # What each address answered, without the key, so Claude can read why a key was refused.
    $checkLog = Join-Path $HOME '.claude-deepseek\key-check.log'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $checkLog) | Out-Null
    Add-Content -LiteralPath $checkLog -Value "$(Get-Date -Format s) $Provider/$Tier key starts '$($key.Substring(0, [Math]::Min(3, $key.Length)))', $($key.Length) characters"
    foreach ($url in $urls) {
        try {
            Invoke-RestMethod -Method Post -Uri (([string]$url).TrimEnd('/') + '/v1/messages') -Headers $headers -Body $body `
                -ContentType 'application/json' -TimeoutSec 30 | Out-Null
            $result = 'ok'; $workingUrl = $url; Add-Content -LiteralPath $checkLog -Value "  $url -> 200"; break
        } catch {
            $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
            $detail = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
            Add-Content -LiteralPath $checkLog -Value "  $url -> $code $($detail -replace '\s+', ' ')"
            if ($code -in 401, 403) { $result = 'rejected'; continue }
            # 402 (no credit) and 429 (busy) still mean the key authenticated at this address.
            if ($code -in 402, 429) { $result = "ok-$code"; $workingUrl = $url; break }
            $lastError = $_.Exception.Message
        }
    }
    if ($result -eq 'rejected') {
        Write-Host "$($p.name) says that key is not valid$(if ($urls.Count -gt 1) { ' at any of its addresses' }). Check you copied all of it, then try again." -ForegroundColor Red
        continue
    }
    [Environment]::SetEnvironmentVariable($keyEnv, $key, 'User')
    # Remember the address that worked and, for a subscription the user bought, make it this provider's default
    # tier: in the user's own settings, which ds_providers.py reads (never in a project).
    if ($workingUrl -and $urls.Count -gt 1 -or ($tierInfo -and $tierInfo.subscription -and $workingUrl)) {
        $settingsPath = if ($env:DS_PROVIDERS_USER) { $env:DS_PROVIDERS_USER } else { Join-Path $HOME '.claude-deepseek\providers.json' }
        $settings = if (Test-Path -LiteralPath $settingsPath) { Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
        if (-not $settings.baseUrls) { $settings | Add-Member -NotePropertyName baseUrls -NotePropertyValue ([pscustomobject]@{}) -Force }
        $settings.baseUrls | Add-Member -NotePropertyName "$Provider/$Tier" -NotePropertyValue $workingUrl -Force
        if ($tierInfo.subscription) {
            if (-not $settings.defaultTiers) { $settings | Add-Member -NotePropertyName defaultTiers -NotePropertyValue ([pscustomobject]@{}) -Force }
            $settings.defaultTiers | Add-Member -NotePropertyName $Provider -NotePropertyValue $Tier -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $settingsPath) | Out-Null
        [IO.File]::WriteAllText($settingsPath, ($settings | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding $false))
    }
    Write-Host ''
    switch -Wildcard ($result) {
        'ok' { Write-Host "Saved as $keyEnv. $($p.name) accepted it$(if ($urls.Count -gt 1) { " at $workingUrl" })." -ForegroundColor Green }
        'ok-402' { Write-Host "Saved as $keyEnv. The key is valid, but the account has no credit yet: add some at https://$($p.keyPage) before workers can run." -ForegroundColor Yellow }
        'ok-429' { Write-Host "Saved as $keyEnv. The key is valid ($($p.name) is busy right now)$(if ($urls.Count -gt 1) { " at $workingUrl" })." -ForegroundColor Green }
        default { Write-Host "Saved as $keyEnv, but couldn't confirm it with $($p.name) ($lastError)." -ForegroundColor Yellow }
    }
    if ($tierInfo -and $tierInfo.subscription -and $workingUrl) { Write-Host "Workers on $($p.name) now use the $Tier by default." -ForegroundColor Green }
    Write-Host 'Only your Windows account can read the key.'
    Write-Host ''
    Write-Host 'You can close this window now.' -ForegroundColor Cyan
    break
}
