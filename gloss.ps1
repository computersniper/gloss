param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$root = $PSScriptRoot
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'GlossX virtual environment is missing. Run scripts\setup.ps1 first.'
    exit 1
}
& $python (Join-Path $root 'cli\gloss.py') @Arguments
exit $LASTEXITCODE
