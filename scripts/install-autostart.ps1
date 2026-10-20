# Registers Friday's Telegram bridge as a Windows scheduled task that starts at
# logon and restarts if it stops. Run this once, from the repo root, in a normal
# (non-admin) PowerShell:
#
#     powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
#
# Remove later with:  Unregister-ScheduledTask -TaskName "Friday" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $root "scripts\start-friday.ps1"
$taskName = "Friday"

# The task runs hidden, so nobody would see a pairing code. Require a pinned owner.
$envFile = Join-Path $root ".env"
$ownerLine = $null
if (Test-Path $envFile) {
    $ownerLine = Get-Content $envFile | Where-Object { $_ -match '^\s*TELEGRAM_OWNER_ID\s*=\s*[1-9][0-9]*\s*$' }
}
if (-not $ownerLine) {
    Write-Host "TELEGRAM_OWNER_ID is not set in .env." -ForegroundColor Red
    Write-Host "Pair once in a visible terminal (python run.py telegram), pin the id it prints"
    Write-Host "as TELEGRAM_OWNER_ID=<id> in .env, then rerun this script."
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Friday personal AI assistant — Telegram bridge" `
    -Force

Write-Host "Registered scheduled task '$taskName'. It will start at your next logon."
Write-Host "Start it now with:  Start-ScheduledTask -TaskName $taskName"
