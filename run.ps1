# Always use the project's Amulet runtime, regardless of PATH or activation.
# Examples: .\run.ps1 preflight_environment.py
#           .\run.ps1 -m unittest discover -s tests
$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot '.venv311\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePath)) {
    throw 'Missing .venv311. See docs/ENVIRONMENT.md for setup.'
}
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try {
    & $runtimePath @args
    if ($null -eq $LASTEXITCODE) {
        throw 'Python did not start. Check filesystem/sandbox access; see docs/ENVIRONMENT.md.'
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
