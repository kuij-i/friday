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
