# Starts Friday's Telegram bridge. Used by the scheduled task and runnable by hand.
# Resolves paths relative to this script so it works wherever the repo lives.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    Write-Host "No .venv found; using system python. (Create one with: python -m venv .venv)"
    $python = "python"
}

& $python "run.py" "telegram"
