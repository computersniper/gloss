param([string]$Python = "python")

$projectRoot = Split-Path -Parent $PSScriptRoot
$buildVenv = Join-Path $projectRoot "build\windows-venv"
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
$previousPath = $env:PATH

Push-Location $projectRoot
try {
    & npm run build:frontend
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-Path -LiteralPath $buildPython)) {
        & $Python -m venv $buildVenv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    & $buildPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $buildPython -m pip install -r backend/requirements.txt -r packaging/requirements-build.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # Conda places SQLite and libffi beside its other native libraries.
    # Make them discoverable when PyInstaller inspects the base interpreter.
    $basePrefix = & $buildPython -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $nativeLibraries = Join-Path $basePrefix "Library\bin"
    if (Test-Path -LiteralPath $nativeLibraries) {
        $env:PATH = "$nativeLibraries;$env:PATH"
    }

    # The backend, as a folder the desktop shell ships as resources/backend.
    & $buildPython -m PyInstaller --noconfirm --clean packaging/GlossX.spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # The Electron shell, packed with the backend into one portable executable.
    & npm --prefix desktop ci --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    # npm ci already installed this exact Electron version.
    $electronDist = Join-Path $projectRoot "desktop\node_modules\electron\dist"
    & npm --prefix desktop run dist:win -- "--config.electronDist=$electronDist"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Copy-Item -Force (Join-Path $projectRoot "dist\desktop\Gloss.exe") (Join-Path $projectRoot "dist\Gloss.exe")

    Write-Host "Gloss executable: $projectRoot\dist\Gloss.exe"
}
finally {
    $env:PATH = $previousPath
    Pop-Location
}
