$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$binaryDirectory = Join-Path $repositoryRoot 'apps\desktop\src-tauri\binaries'
$targetBinary = Join-Path $binaryDirectory 'pipedeckd-x86_64-pc-windows-msvc.exe'

New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null

Push-Location $repositoryRoot
try {
    uv run pyinstaller `
        --clean `
        --noconfirm `
        --onefile `
        --noconsole `
        --name pipedeckd `
        --paths src `
        src\pipedeck\__main__.py `
        --add-data "src\pipedeck\gitlab_ci\schema\ci.json;pipedeck/gitlab_ci/schema"
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'dist\pipedeckd.exe') -Destination $targetBinary -Force
}
finally {
    Pop-Location
}

Write-Output $targetBinary
