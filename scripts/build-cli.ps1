$ErrorActionPreference = 'Stop'

# Keep this script ASCII-compatible with Windows PowerShell 5.1 (system code page).
# The installer bundles the stable filename and registers resources\bin on PATH.

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$binaryDirectory = Join-Path $repositoryRoot 'apps\desktop\src-tauri\binaries'
$targetBinary = Join-Path $binaryDirectory 'pipedeck-x86_64-pc-windows-msvc.exe'
$stableBinary = Join-Path $binaryDirectory 'pipedeck.exe'

New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null

Push-Location $repositoryRoot
try {
    uv run pyinstaller `
        --clean `
        --noconfirm `
        --onefile `
        --name pipedeck `
        --paths src `
        src\pipedeck\cli.py `
        --add-data "src\pipedeck\gitlab_ci\schema\ci.json;pipedeck/gitlab_ci/schema"
    if ($LASTEXITCODE -ne 0) { throw "CLI build failed with exit code $LASTEXITCODE" }
    $builtBinary = Join-Path $repositoryRoot 'dist\pipedeck.exe'
    if (-not (Test-Path -LiteralPath $builtBinary -PathType Leaf)) { throw "CLI build did not produce $builtBinary" }
    $expectedHash = (Get-FileHash -LiteralPath $builtBinary -Algorithm SHA256).Hash
    foreach ($destination in @($targetBinary, $stableBinary)) {
        Copy-Item -LiteralPath $builtBinary -Destination $destination -Force
        $actualHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($actualHash -ne $expectedHash) { throw "CLI copy verification failed: $destination" }
        Write-Output "Verified CLI SHA256 $actualHash -> $destination"
    }
}
finally {
    Pop-Location
}

Write-Output $targetBinary
