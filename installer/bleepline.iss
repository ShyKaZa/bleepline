; Bleepline setup (Inno Setup 6.7+). build.py compiles it after PyInstaller:
;     ISCC /DAppVersion=1.2.0 installer\bleepline.iss   ->  dist\Bleepline-Setup.exe
; Installs for the current Windows user only, so no administrator rights are needed. Running a newer setup
; updates the installed copy in place; downloads and settings (in %LOCALAPPDATA%\Bleepline) are kept.
; Silent install: Bleepline-Setup.exe /VERYSILENT [/DIR="path"] [/TASKS="desktopicon,checkupdates"]

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Bleepline"
#define AppExe "Bleepline.exe"
#define ProjectUrl "https://github.com/ShyKaZa/bleepline"

[Setup]
AppId={#AppName}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Gorgho
AppCopyright=Copyright (c) 2026 Gorgho
AppPublisherURL={#ProjectUrl}
AppSupportURL={#ProjectUrl}/issues
AppUpdatesURL={#ProjectUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany=Gorgho
VersionInfoDescription={#AppName} Setup
PrivilegesRequired=lowest
DefaultDirName={code:DefaultDir}
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableReadyPage=yes
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Bleepline-Setup
SetupIconFile=..\assets\bleepline.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern dark includetitlebar
WizardImageFile=..\assets\installer-side.png
WizardSmallImageFile=..\assets\installer-small.png
Compression=lzma2/ultra64
SolidCompression=yes
; The compiler is 32-bit; compress in its 64-bit helper so the big dictionary fits in memory.
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
SetupLogging=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "checkupdates"; Description: "Check GitHub once a day for a new {#AppName} version (sends nothing about you or what you hear; can be changed in Options)"; GroupDescription: "Updates:"

[InstallDelete]
; The previous version's libraries: the set of files changes between versions.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Bleepline\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Real-time profanity filter"; AppUserModelID: "Bleepline.App"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "Bleepline.App"

[Registry]
; Read by the app (app/updates.py) until the setting is changed in Options.
Root: HKCU; Subkey: "Software\{#AppName}"; ValueType: dword; ValueName: "CheckForUpdates"; ValueData: 1; Tasks: checkupdates; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#AppName}"; ValueType: dword; ValueName: "CheckForUpdates"; ValueData: 0; Tasks: not checkupdates; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; "Start Bleepline when Windows starts" (Options tab) creates this shortcut.
Type: files; Name: "{userstartup}\{#AppName}.lnk"

[Code]
const
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';
  OldInstallerKey = 'Bleepline';  { Bleepline 1.1 used its own installer }
  VoiceGuardKey = 'VoiceGuard';   { 1.0, before the rename }
  AppMutexName = 'Bleepline.App'; { held by the running app, see main.py }

var
  HadStartupShortcut: Boolean;
  RemoveData, RemovePlugin: Boolean;

function InstallLocation(Key: String): String;
begin
  if not RegQueryStringValue(HKCU, UninstallRoot + Key, 'InstallLocation', Result) then
    Result := '';
  if Result <> '' then
    Result := RemoveBackslashUnlessRoot(Result);
end;

{ First install: where 1.1 was installed, if it was; otherwise %LOCALAPPDATA%\Programs\Bleepline. }
function DefaultDir(Param: String): String;
begin
  Result := InstallLocation(OldInstallerKey);
  if Result = '' then
    Result := ExpandConstant('{autopf}\{#AppName}');
end;

{ Ask a running Bleepline to close (it saves its settings on the way), then make sure it's gone. }
procedure CloseBleepline();
var
  ResultCode, I: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 128 then
    Exit;  { not running (other errors, e.g. no window to ask, fall through to the forced close) }
  if CheckForMutexes(AppMutexName) then begin
    for I := 1 to 40 do begin
      if not CheckForMutexes(AppMutexName) then
        Break;
      Sleep(250);
    end;
  end else
    Sleep(2000);  { versions before 1.2 don't hold the mutex }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ Only the files the 1.1 installer put there, so a folder shared with other files keeps them. }
procedure DeleteOldInstallerFiles(Dir: String);
begin
  DeleteFile(Dir + '\{#AppExe}');
  DelTree(Dir + '\_internal', True, True, True);
  DelTree(Dir + '\obs-plugin', True, True, True);
  DeleteFile(Dir + '\LICENSE');
  DeleteFile(Dir + '\THIRD_PARTY_NOTICES.txt');
  RemoveDir(Dir);  { only succeeds if nothing else is left in it }
end;

procedure MigrateOldVersions();
var
  OldDir, Command, Exe: String;
  ResultCode: Integer;
begin
  if RegKeyExists(HKCU, UninstallRoot + OldInstallerKey) then begin
    OldDir := InstallLocation(OldInstallerKey);
    if (OldDir <> '') and (CompareText(OldDir, WizardDirValue) <> 0) then
      DeleteOldInstallerFiles(OldDir);
    RegDeleteKeyIncludingSubkeys(HKCU, UninstallRoot + OldInstallerKey);
  end;
  { Voice Guard 1.0 lives in its own folder: run its uninstaller, keeping models and settings, which
    Bleepline moves over on first start. }
  if RegQueryStringValue(HKCU, UninstallRoot + VoiceGuardKey, 'QuietUninstallString', Command) then begin
    OldDir := InstallLocation(VoiceGuardKey);
    if (OldDir <> '') and (CompareText(OldDir, WizardDirValue) <> 0) and (Copy(Command, 1, 1) = '"') then begin
      Exe := Copy(Command, 2, Length(Command));
      Exe := Copy(Exe, 1, Pos('"', Exe) - 1);
      if FileExists(Exe) then
        Exec(Exe, '--uninstall --silent --keep-data', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
    RegDeleteKeyIncludingSubkeys(HKCU, UninstallRoot + VoiceGuardKey);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseBleepline();
  HadStartupShortcut := FileExists(ExpandConstant('{userstartup}\{#AppName}.lnk'));
  MigrateOldVersions();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { "Start with Windows" keeps working if the install folder changed. }
  if (CurStep = ssPostInstall) and HadStartupShortcut then
    CreateShellLink(ExpandConstant('{userstartup}\{#AppName}.lnk'), '{#AppName}', ExpandConstant('{app}\{#AppExe}'),
      '--startup', ExpandConstant('{app}'), '', 0, SW_SHOWNORMAL);
end;

function InitializeUninstall(): Boolean;
begin
  CloseBleepline();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, PluginRoot: String;
begin
  DataDir := ExpandConstant('{localappdata}\{#AppName}');
  PluginRoot := ExpandConstant('{commonappdata}\obs-studio\plugins');
  if (CurUninstallStep = usUninstall) and not UninstallSilent then begin
    if DirExists(DataDir) then
      RemoveData := MsgBox('Also remove the speech models, GPU runtime, settings and word lists Bleepline ' +
        'downloaded or saved?' + #13#10#13#10 + DataDir, mbConfirmation, MB_YESNO) = IDYES;
    if DirExists(PluginRoot + '\bleepline-filter') or DirExists(PluginRoot + '\voice-guard-filter') then
      RemovePlugin := MsgBox('Also remove the Bleepline filter from OBS Studio?', mbConfirmation, MB_YESNO) = IDYES;
  end;
  if CurUninstallStep = usPostUninstall then begin
    if RemoveData then
      DelTree(DataDir, True, True, True);
    if RemovePlugin then begin
      DelTree(PluginRoot + '\bleepline-filter', True, True, True);
      DelTree(PluginRoot + '\voice-guard-filter', True, True, True);
      RemoveDir(PluginRoot);                  { tidy up OBS's folders only if they're now empty }
      RemoveDir(ExtractFileDir(PluginRoot));
    end;
  end;
end;
