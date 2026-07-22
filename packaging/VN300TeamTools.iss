#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "VN300 Team Tools"
#define AppPublisher "SRT26"
#define AppExeName "VN300TeamTools.exe"

[Setup]
AppId={{C2FC304A-8BA5-41B2-B7C7-3BA0BFE08F32}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\VN300 Team Tools
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=VN300-Team-Tools-Setup-{#AppVersion}
SetupIconFile=assets\VN300TeamTools.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
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
Source: "assets\VN300TeamTools.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\APP_VERSION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
