#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Sooner Racing Telemetry"
#define AppPublisher "Sooner Racing Team"
#define AppExeName "VN300TeamTools.exe"

[Setup]
AppId={{C2FC304A-8BA5-41B2-B7C7-3BA0BFE08F32}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Sooner Racing Telemetry
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Sooner-Racing-Telemetry-Setup-{#AppVersion}
SetupIconFile=assets\SoonerRacingTelemetry.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\bin\VN300TeamTools.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\bin\VN300Analyzer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\bin\VN300UpdateHelper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\SoonerRacingTelemetry.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\SoonerRacingTelemetry.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\APP_VERSION"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{userprograms}\VN300 Team Tools\*.lnk"
Type: dirifempty; Name: "{userprograms}\VN300 Team Tools"
Type: files; Name: "{autodesktop}\VN300 Team Tools.lnk"

[Icons]
Name: "{userprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\SoonerRacingTelemetry.ico"
Name: "{userprograms}\{#AppName}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\SoonerRacingTelemetry.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure ForceCloseSRTProcesses;
var
  Attempt: Integer;
  ResultCode: Integer;
begin
  for Attempt := 1 to 3 do
  begin
    if not Exec(
      ExpandConstant('{sys}\taskkill.exe'),
      '/F /T /IM "{#AppExeName}"',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) then
      ResultCode := -1;
    Sleep(500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ForceCloseSRTProcesses;
  Result := '';
end;
