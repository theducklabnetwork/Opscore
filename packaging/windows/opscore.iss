; Per-user installer for the self-contained folder from scripts/build_windows.ps1.
; The version is read from the packaged RELEASE file at compile time, so the
; installer cannot drift from the folder it packages. Optionally pass
; /DOPSCORE_VERSION=<version> to require an exact expected version.
;
; Candidate builds (the default) package a candidate folder and write a
; candidate installer, leaving the published release artifacts untouched:
;   ISCC.exe /DOPSCORE_PACKAGE_NAME=Opscore-candidate packaging\windows\opscore.iss
; A release build packages dist\Opscore:
;   ISCC.exe packaging\windows\opscore.iss

#define OPSCORE_DIST "..\..\dist"
#ifndef OPSCORE_PACKAGE_NAME
  #define OPSCORE_PACKAGE_NAME "Opscore"
#endif
#define OPSCORE_PACKAGE OPSCORE_DIST + "\" + OPSCORE_PACKAGE_NAME
#define OPSCORE_RELEASE OPSCORE_PACKAGE + "\RELEASE"
#define OPSCORE_RELEASE_HANDLE FileOpen(OPSCORE_RELEASE)
#if OPSCORE_RELEASE_HANDLE == 0
  #error "Build the package folder first: its RELEASE file is missing."
#endif
#define OPSCORE_PACKAGE_VERSION Trim(FileRead(OPSCORE_RELEASE_HANDLE))
#expr FileClose(OPSCORE_RELEASE_HANDLE)
#ifndef OPSCORE_PACKAGE_VERSION
  #error "The packaged RELEASE file is empty."
#endif
#ifdef OPSCORE_VERSION
  #if OPSCORE_VERSION != OPSCORE_PACKAGE_VERSION
    #error "OPSCORE_VERSION does not match the packaged RELEASE file."
  #endif
#else
  #define OPSCORE_VERSION OPSCORE_PACKAGE_VERSION
#endif

; The installer file name follows the package folder: a candidate package
; produces a candidate installer and never replaces a published one.
#ifndef OPSCORE_OUTPUT_NAME
  #if OPSCORE_PACKAGE_NAME == "Opscore"
    #define OPSCORE_OUTPUT_NAME "Opscore-Setup-" + OPSCORE_VERSION
  #else
    #define OPSCORE_OUTPUT_NAME "Opscore-Setup-" + OPSCORE_VERSION + "-candidate"
  #endif
#endif
#define OPSCORE_OUTPUT_PATH OPSCORE_DIST + "\" + OPSCORE_OUTPUT_NAME + ".exe"

; Never silently replace an existing installer. Pass /DOPSCORE_ALLOW_OVERWRITE
; to allow it deliberately.
#ifndef OPSCORE_ALLOW_OVERWRITE
  #if FileExists(OPSCORE_OUTPUT_PATH)
    #error "The installer output already exists. Remove it or pass /DOPSCORE_ALLOW_OVERWRITE."
  #endif
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
OutputDir={#OPSCORE_DIST}
OutputBaseFilename={#OPSCORE_OUTPUT_NAME}
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
Source: "{#OPSCORE_PACKAGE}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Launch pythonw.exe directly: unlike Opscore.cmd, this never opens a console.
Name: "{autoprograms}\Opscore"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\packaging\opscore_desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\opscore.ico"
Name: "{autodesktop}\Opscore"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\packaging\opscore_desktop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\opscore.ico"; Tasks: desktopicon

; Do not add an UninstallDelete entry for {localappdata}\Opscore.
; The database, logs, backups and other user data must survive upgrades and removal.

[Code]
var
  DatabasePage: TInputOptionWizardPage;

function ExistingDatabasePath(): String;
begin
  Result := ExpandConstant('{localappdata}\Opscore\store_ops.db');
end;

function ShouldShowDatabasePage(): Boolean;
begin
  { Offer the keep/fresh choice only when an existing database is detected. }
  Result := FileExists(ExistingDatabasePath());
end;

procedure InitializeWizard();
begin
  DatabasePage := CreateInputOptionPage(
    wpReady,
    'Opscore database',
    'An existing database was found',
    'Choose whether to keep your existing data or start fresh.',
    True, False);
  DatabasePage.Add('Use the existing database (recommended)');
  DatabasePage.Add('Create a fresh database (the existing one is kept as a numbered backup)');
  DatabasePage.SelectedValueIndex := 0;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = DatabasePage.ID) and not ShouldShowDatabasePage();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Choice: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  if not ShouldShowDatabasePage() then
    exit;
  { Record the wizard choice for the first launch; never touch the database
    itself here, because a running Opscore instance may still hold it open.
    The launcher consumes and deletes this one-shot marker. }
  if DatabasePage.SelectedValueIndex = 1 then
    Choice := 'fresh'
  else
    Choice := 'keep';
  ForceDirectories(ExpandConstant('{localappdata}\Opscore'));
  SaveStringToFile(
    ExpandConstant('{localappdata}\Opscore\db-choice.json'),
    '{"version": "{#OPSCORE_VERSION}", "choice": "' + Choice + '"}' + #10,
    False);
end;
