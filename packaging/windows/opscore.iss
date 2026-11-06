; Per-user installer for the self-contained folder from scripts/build_windows.ps1.
; Pass the version written to dist\Opscore\RELEASE:
;   ISCC.exe /DOPSCORE_VERSION=1.0 packaging\windows\opscore.iss

#ifndef OPSCORE_VERSION
  #error "Define OPSCORE_VERSION with the value from dist\Opscore\RELEASE."
#endif

[Setup]
AppId={{7B9D53AA-62C3-4DF3-9F6B-45D89F717F24}
AppName=Opscore
AppVersion={#OPSCORE_VERSION}
AppPublisher=DuckLab
DefaultDirName={localappdata}\Programs\Opscore
DefaultGroupName=Opscore
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=Opscore-Setup-{#OPSCORE_VERSION}
SetupIconFile=..\..\assets\opscore.ico
UninstallDisplayIcon={app}\assets\opscore.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\Opscore

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\Opscore\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Launch pythonw.exe directly: unlike Opscore.cmd, this never opens a console.
Name: "{autoprograms}\Opscore"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\packaging\opscore_desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\opscore.ico"
Name: "{autodesktop}\Opscore"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\packaging\opscore_desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\opscore.ico"; Tasks: desktopicon

; Do not add an UninstallDelete entry for {localappdata}\Opscore.
; The database, logs, backups and other user data must survive upgrades and removal.
