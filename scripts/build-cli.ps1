$ErrorActionPreference = 'Stop'

# 构建 Pipedeck CLI 单文件（pipedeck.exe），随桌面安装包进 resources\bin，
# 由 installer-hooks.nsi 在安装时写用户 PATH。

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$binaryDirectory = Join-Path $repositoryRoot 'apps\desktop\src-tauri\binaries'
$targetBinary = Join-Path $binaryDirectory 'pipedeck-x86_64-pc-windows-msvc.exe'

New-Item -ItemType Directory -Path $binaryDirectory -Force | Out-Null

Push-Location $repositoryRoot
try {
    uv run pyinstaller `
        --clean `
        --noconfirm `
        --onefile `
        --name pipedeck `
        --paths src `
        src\pipedeck\cli.py
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'dist\pipedeck.exe') -Destination $targetBinary -Force
}
finally {
    Pop-Location
}

Write-Output $targetBinary
