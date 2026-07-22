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
         StringStruct(u'FileDescription', u'VN300 Team Tools'),
         StringStruct(u'FileVersion', u'$Version'),
         StringStruct(u'InternalName', u'VN300TeamTools'),
         StringStruct(u'OriginalFilename', u'VN300TeamTools.exe'),
         StringStruct(u'ProductName', u'VN300 Team Tools'),
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
py -3 -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistBin `
    --workpath $PyInstallerWork `
    (Join-Path $Root "packaging\VN300TeamTools.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
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

$Installer = Join-Path $DistInstaller "VN300-Team-Tools-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "The expected installer was not produced: $Installer"
}
$Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $([IO.Path]::GetFileName($Installer))" | Set-Content -LiteralPath "$Installer.sha256" -Encoding ASCII
Write-Host "Built $Installer"
Write-Host "SHA256 $Hash"
