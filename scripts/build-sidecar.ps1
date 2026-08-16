$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$binaryDirectory = Join-Path $repositoryRoot 'apps\desktop\src-tauri\binaries'
$targetBinary = Join-Path $binaryDirectory 'locald-x86_64-pc-windows-msvc.exe'

New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null

Push-Location $repositoryRoot
try {
    uv run pyinstaller `
        --clean `
        --noconfirm `
        --onefile `
        --name locald `
        --paths src `
        src\tripguru_local\__main__.py
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'dist\locald.exe') -Destination $targetBinary -Force
}
finally {
    Pop-Location
}

Write-Output $targetBinary
