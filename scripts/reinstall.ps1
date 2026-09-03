# 全自动重装 Pipedeck 桌面客户端（静默，无任何交互弹窗）
# 用法:  powershell -ExecutionPolicy Bypass -File scripts\reinstall.ps1 -Installer <setup.exe路径> [-Launch]
# 由代理在迭代新客户端后自行执行，替代用户手动点击安装向导。

param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$InstallDir = 'D:\apps\Pipedeck',
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
function Write-Step($msg) { Write-Host "[reinstall] $msg" -ForegroundColor Cyan }

if (-not (Test-Path $Installer)) { throw "找不到安装包: $Installer" }
$Installer = (Resolve-Path $Installer).Path

# 1. 杀掉正在运行的桌面客户端与 sidecar（文件占用 + 单实例锁）
Write-Step "停止运行中的 pipedeck-desktop / pipedeckd ..."
Get-Process -Name 'pipedeck-desktop','pipedeckd' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 2. 静默卸载旧版本（若有）
$uninstaller = Join-Path $InstallDir 'uninstall.exe'
if (Test-Path $uninstaller) {
    Write-Step "静默卸载旧版本: $uninstaller /S"
    $p = Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru
    Write-Step "卸载退出码: $($p.ExitCode)"
    Start-Sleep -Seconds 2
}

# 3. 确保安装目录可写（卸载可能残留只读项）
if (Test-Path $InstallDir) {
    Get-ChildItem $InstallDir -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# 4. 静默安装新版本（/S 无界面；/D= 指定安装目录，必须是最后一个参数）
Write-Step "静默安装: $Installer /S /D=$InstallDir"
$args = @('/S', "/D=$InstallDir")
$p = Start-Process -FilePath $Installer -ArgumentList $args -Wait -PassThru
Write-Step "安装退出码: $($p.ExitCode)"
if ($p.ExitCode -ne 0) { throw "安装失败，退出码 $($p.ExitCode)" }

# 5. 校验产物与 PE 子系统（sidecar 与桌面端都必须是 GUI 子系统=2，无 cmd 窗口）
function Get-PESubsystem($path) {
    $fs = [System.IO.File]::OpenRead($path)
    try {
        $br = New-Object System.IO.BinaryReader($fs)
        $fs.Seek(0x3c, 'Begin') | Out-Null
        $peOff = $br.ReadInt32()
        $fs.Seek($peOff + 24 + 68, 'Begin') | Out-Null
        return $br.ReadUInt16()
    } finally { $fs.Close() }
}
$desktop = Join-Path $InstallDir 'pipedeck-desktop.exe'
$sidecar = Join-Path $InstallDir 'pipedeckd.exe'
foreach ($exe in @($desktop, $sidecar)) {
    if (-not (Test-Path $exe)) { throw "缺少产物: $exe" }
    $sub = Get-PESubsystem $exe
    Write-Step "$(Split-Path $exe -Leaf) 子系统 = $sub (2=GUI, 3=Console)"
    if ($sub -ne 2) { throw "$exe 不是 GUI 子系统，启动会弹 cmd 窗口！" }
}
if (-not (Test-Path (Join-Path $InstallDir 'resources\bin\pipedeck.exe'))) {
    throw "缺少 CLI 产物: resources\bin\pipedeck.exe"
}

# 6. 可选：启动并校验没有控制台宿主
if ($Launch) {
    Write-Step "启动客户端 ..."
    Start-Process -FilePath $desktop
    Start-Sleep -Seconds 6
    $app = Get-Process -Name 'pipedeck-desktop' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $app) { throw "客户端未启动" }
    $conhost = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'conhost.exe' -and $_.ParentProcessId -eq $app.Id }
    if ($conhost) { Write-Host "警告: 客户端仍挂有 conhost 子进程，可能有 cmd 窗口" -ForegroundColor Yellow }
    else { Write-Step "验证通过: 无 conhost 子进程，不会弹 cmd 窗口" }
    Write-Step "客户端已启动 (PID $($app.Id))"
}

Write-Step "重装完成。安装目录: $InstallDir"
