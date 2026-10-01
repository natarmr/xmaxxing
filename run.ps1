param(
    [string[]]$Args,
    [int]$Minutes = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating virtualenv..."
    py -m venv $venv
    if (-not (Test-Path $python)) { throw "venv creation failed - is Python installed as 'py'?" }
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $root "requirements.txt") --quiet
    & $python -m playwright install chromium
    Write-Host "Setup done."
}

if (-not $Args -or $Args.Count -eq 0) {
    $Args = @("run", "--minutes", "$Minutes")
}

& $python -m xmaxxing @Args
exit $LASTEXITCODE