param(
    [string]$IsccPath = $env:ISCC_PATH
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content -Raw -LiteralPath (Join-Path $Root "APP_VERSION")).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "APP_VERSION must use MAJOR.MINOR.PATCH format."
}

$VersionParts = $Version.Split('.') | ForEach-Object { [int]$_ }
$VersionTuple = "$($VersionParts[0]), $($VersionParts[1]), $($VersionParts[2]), 0"
$GeneratedDir = Join-Path $Root "build\generated"
$PyInstallerWork = Join-Path $Root "build\pyinstaller"
$DistBin = Join-Path $Root "dist\bin"
$DistInstaller = Join-Path $Root "dist\installer"

foreach ($Path in @($GeneratedDir, $PyInstallerWork, $DistBin, $DistInstaller)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

$VersionFile = Join-Path $GeneratedDir "windows_version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($VersionTuple),
    prodvers=($VersionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'SRT26'),
         StringStruct(u'FileDescription', u'Sooner Racing Telemetry'),
         StringStruct(u'FileVersion', u'$Version'),
         StringStruct(u'InternalName', u'VN300TeamTools'),
         StringStruct(u'OriginalFilename', u'VN300TeamTools.exe'),
         StringStruct(u'ProductName', u'Sooner Racing Telemetry'),
         StringStruct(u'ProductVersion', u'$Version')]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -LiteralPath $VersionFile -Encoding UTF8

py -3 -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing. Run: py -3 -m pip install -r packaging\requirements-build.txt"
}

$env:VN300_VERSION_FILE = $VersionFile
$env:VN300_PROJECT_ROOT = $Root
$BuildPython = (py -3 -c "import sys; print(sys.executable)").Trim()
$OriginalBuildPath = $env:PATH
try {
    # DLL discovery must not pick up incompatible ICU/Qt libraries from tools on PATH.
    $env:PATH = @((Split-Path $BuildPython), "$env:SystemRoot\System32", $env:SystemRoot) -join ';'
    & $BuildPython -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $DistBin `
        --workpath $PyInstallerWork `
        (Join-Path $Root "packaging\VN300TeamTools.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
} finally {
    $env:PATH = $OriginalBuildPath
}

# Exercise the actual frozen Qt imports and main window before publishing an installer.
$SmokeResult = Join-Path $GeneratedDir "desktop-smoke.json"
$SmokeProcess = Start-Process -FilePath (Join-Path $DistBin "VN300TeamTools.exe") `
    -ArgumentList @('--smoke-test', ('"' + $SmokeResult + '"')) -WindowStyle Hidden -PassThru
if (-not $SmokeProcess.WaitForExit(60000)) {
    Stop-Process -Id $SmokeProcess.Id -Force -ErrorAction SilentlyContinue
    throw "Packaged desktop startup timed out."
}
if ($SmokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SmokeResult)) {
    throw "Packaged desktop startup failed; refusing to build an installer."
}
if (-not (Get-Content -LiteralPath $SmokeResult -Raw | ConvertFrom-Json).window_visible) {
    throw "Packaged desktop did not open its main window."
}

if (-not $IsccPath) {
    $IsccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($IsccCommand) {
        $IsccPath = $IsccCommand.Source
    }
}
if (-not $IsccPath) {
    foreach ($Candidate in @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) {
            $IsccPath = $Candidate
            break
        }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "Inno Setup 6 was not found. Install it or set ISCC_PATH."
}

& $IsccPath "/DAppVersion=$Version" (Join-Path $Root "packaging\VN300TeamTools.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$Installer = Join-Path $DistInstaller "Sooner-Racing-Telemetry-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "The expected installer was not produced: $Installer"
}
$LegacyInstaller = Join-Path $DistInstaller "VN300-Team-Tools-Setup-$Version.exe"
Copy-Item -LiteralPath $Installer -Destination $LegacyInstaller -Force
foreach ($ReleaseInstaller in @($Installer, $LegacyInstaller)) {
    $Hash = (Get-FileHash -LiteralPath $ReleaseInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $([IO.Path]::GetFileName($ReleaseInstaller))" | Set-Content -LiteralPath "$ReleaseInstaller.sha256" -Encoding ASCII
}
Write-Host "Built $Installer"
Write-Host "Built compatibility alias $LegacyInstaller"
