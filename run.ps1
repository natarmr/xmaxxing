param(
    # One array param, ValueFromRemainingArguments, and nothing else declared.
    # Two earlier versions both failed and neither failed loudly:
    #   - `[string[]]$Args` never binds (PowerShell reserves $Args), so
    #     `.\run.ps1 selftest` silently ran `run --minutes 30`.
    #   - adding `[int]$Minutes` alongside it made PowerShell bind the first
    #     positional argument to Minutes, so `.\run.ps1 selftest` died casting
    #     "selftest" to Int32.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
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

$configFile = Join-Path $root "config.toml"
if (-not (Test-Path $configFile)) {
    Copy-Item (Join-Path $root "config.example.toml") $configFile
    Write-Host "Created config.toml from the template - edit [profile] before your first run."
}

if (-not $Rest -or $Rest.Count -eq 0) {
    $Rest = @("run", "--minutes", "30")
}

# No `-Minutes` shorthand on purpose: mixing a script-level flag with a
# pass-through command line produced ambiguous orderings. `run --minutes 60`
# forwards correctly now that nothing else is declared.
& $python -m xmaxxing @Rest
exit $LASTEXITCODE