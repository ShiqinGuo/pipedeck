# Upgrade in place, preserving LOCALAPPDATA\Pipedeck. Use -WhatIf for a read-only preview.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$InstallDir = 'D:\apps\Pipedeck',
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
function Write-Step([string]$Message) { Write-Host "[reinstall] $Message" -ForegroundColor Cyan }
function Get-NormalizedPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}
function Test-SamePath([string]$Left, [string]$Right) {
    return [string]::Equals((Get-NormalizedPath $Left), (Get-NormalizedPath $Right), [StringComparison]::OrdinalIgnoreCase)
}
function Test-WithinPath([string]$Path, [string]$Root) {
    $prefix = (Get-NormalizedPath $Root) + [System.IO.Path]::DirectorySeparatorChar
    return (Get-NormalizedPath $Path).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}
function Assert-NoLinkedParents([string]$Path) {
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Installation path must not traverse a junction or symbolic link: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}
function Get-PESubsystem([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $reader = New-Object System.IO.BinaryReader($stream)
        $stream.Seek(0x3c, 'Begin') | Out-Null
        $peOffset = $reader.ReadInt32()
        $stream.Seek($peOffset + 24 + 68, 'Begin') | Out-Null
        return $reader.ReadUInt16()
    }
    finally { $stream.Close() }
}

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) { throw "Installer not found: $Installer" }
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$installerInfo = (Get-Item -LiteralPath $Installer).VersionInfo
if ([System.IO.Path]::GetExtension($Installer) -ne '.exe' -or $installerInfo.ProductName -ne 'Pipedeck') {
    throw 'Expected a Pipedeck NSIS .exe installer.'
}
$expectedVersion = $installerInfo.ProductVersion
if (-not $expectedVersion) { throw 'Installer does not declare a product version.' }
if (-not [System.IO.Path]::IsPathRooted($InstallDir) -or $InstallDir.StartsWith('\\')) {
    throw 'InstallDir must be a local absolute path.'
}
$InstallDir = Get-NormalizedPath $InstallDir
$protectedRoots = @(
    [System.IO.Path]::GetPathRoot($InstallDir), $env:USERPROFILE, $env:LOCALAPPDATA,
    (Split-Path -Parent $PSScriptRoot)
)
foreach ($root in $protectedRoots) {
    if ($root -and (Test-SamePath $InstallDir $root)) { throw "InstallDir cannot be a shared root: $InstallDir" }
}
$stateDirectory = Join-Path $env:LOCALAPPDATA 'Pipedeck'
if ((Test-SamePath $InstallDir $stateDirectory) -or (Test-WithinPath $InstallDir $stateDirectory)) {
    throw 'InstallDir cannot be the Pipedeck state directory or a directory inside it.'
}
if (Test-WithinPath $Installer $InstallDir) { throw 'Keep the installer outside the installation directory.' }
Assert-NoLinkedParents $InstallDir
$desktop = Join-Path $InstallDir 'pipedeck-desktop.exe'
$sidecar = Join-Path $InstallDir 'pipedeckd.exe'
$cli = Join-Path $InstallDir 'resources\bin\pipedeck.exe'
foreach ($path in @($desktop, $sidecar, $cli)) { Assert-NoLinkedParents $path }
if (Test-Path -LiteralPath $InstallDir) {
    if (-not (Test-Path -LiteralPath $InstallDir -PathType Container)) { throw 'InstallDir is not a directory.' }
    if (@(Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1).Count -gt 0) {
        if (-not (Test-Path -LiteralPath $desktop -PathType Leaf)) { throw 'Nonempty InstallDir does not contain a Pipedeck desktop installation.' }
        $existingInfo = (Get-Item -LiteralPath $desktop).VersionInfo
        if ($existingInfo.ProductName -ne 'Pipedeck') { throw 'Existing executable is not Pipedeck.' }
        if ([version]$existingInfo.ProductVersion -gt [version]$expectedVersion) { throw 'Refusing to downgrade the installed client.' }
        Write-Step "Current version: $($existingInfo.ProductVersion)"
    }
}
$registryRoots = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)
foreach ($registryRoot in $registryRoots) {
    $registered = Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
        Where-Object { $_.DisplayName -eq 'Pipedeck' -and $_.InstallLocation }
    foreach ($entry in $registered) {
        if (-not (Test-SamePath $InstallDir $entry.InstallLocation.Trim('"'))) {
            throw "Pipedeck is registered at $($entry.InstallLocation); pass that location as InstallDir."
        }
    }
}
$ownedExecutablePaths = @($desktop, $sidecar, $cli)
function Test-OwnedProcess($Process) {
    if (-not $Process -or -not $Process.ExecutablePath) { return $false }
    foreach ($path in $ownedExecutablePaths) {
        if (Test-SamePath $Process.ExecutablePath $path) { return $true }
    }
    return $false
}
$listeners = @(Get-NetTCPConnection -LocalPort 7421 -State Listen -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if (-not (Test-OwnedProcess $owner)) { throw 'Port 7421 belongs to a different installation or development server. Stop that server before upgrading.' }
}
Write-Step "Installer: $Installer"
Write-Step "Target: $InstallDir -> $expectedVersion"
if (-not $PSCmdlet.ShouldProcess($InstallDir, "Upgrade Pipedeck to $expectedVersion and verify installed files")) { return }

# Only close processes whose executable path belongs to this installation.
$processes = Get-CimInstance Win32_Process -Filter "Name='pipedeck-desktop.exe' OR Name='pipedeckd.exe' OR Name='pipedeck.exe'"
foreach ($candidate in $processes) {
    if (-not (Test-OwnedProcess $candidate)) { continue }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.ProcessId)"
    if (-not (Test-OwnedProcess $current)) { continue }
    $process = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    Write-Step "Stopping owned $($candidate.Name) (PID $($candidate.ProcessId))"
    if ($candidate.Name -eq 'pipedeck-desktop.exe' -and $process.CloseMainWindow()) {
        $process.WaitForExit(10000) | Out-Null
    }
    if (-not $process.HasExited) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.ProcessId)"
        if (Test-OwnedProcess $current) {
            Stop-Process -Id $candidate.ProcessId -Force
            $process.WaitForExit(10000) | Out-Null
        }
    }
}

# NSIS handles the in-place upgrade. Do not uninstall or recursively clear files.
# NSIS requires /D= to be the final argument; it consumes the rest of the command line.
$installation = Start-Process -FilePath $Installer -ArgumentList @('/S', "/D=$InstallDir") -WindowStyle Hidden -Wait -PassThru
if ($installation.ExitCode -ne 0) { throw "Installation failed with exit code $($installation.ExitCode)" }
foreach ($executable in @($desktop, $sidecar, $cli)) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Missing installed executable: $executable" }
    $expectedSubsystem = if ($executable -eq $cli) { 3 } else { 2 }
    if ((Get-PESubsystem $executable) -ne $expectedSubsystem) { throw "Unexpected PE subsystem: $executable" }
}
$installedVersion = (Get-Item -LiteralPath $desktop).VersionInfo.ProductVersion
if ($installedVersion -ne $expectedVersion) { throw "Expected version $expectedVersion; installed $installedVersion" }
Write-Step "Installed desktop version: $installedVersion; desktop, sidecar and CLI are present."
if ($Launch) {
    Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $session = Invoke-RestMethod -Uri 'http://127.0.0.1:7421/api/v1/session' -TimeoutSec 1
            if ($session.version -ne $expectedVersion) { throw "Control service reports unexpected version $($session.version)" }
            $listeners = @(Get-NetTCPConnection -LocalPort 7421 -State Listen -ErrorAction SilentlyContinue)
            if ($listeners.Count -eq 0) { throw 'Control service has no listener.' }
            foreach ($listener in $listeners) {
                $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
                if (-not (Test-OwnedProcess $owner)) { throw 'Control service does not belong to the installed client.' }
            }
            $ready = $true
            break
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Installed control service did not become ready with the expected version.' }
    Write-Step "Installed control service is ready: $expectedVersion"
}
Write-Step "Upgrade completed: $InstallDir"
