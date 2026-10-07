; Asset Studio - one installer with everything in it.
;
; Built by installer/build_installer.py, which stages the app and passes StageDir/AppVersion/OutDir:
;   ISCC.exe /DStageDir=<stage> /DAppVersion=2026.9.24 /DOutDir=<out> installer\AssetStudio.iss
;
; What is inside: the app, its UI already built, Electron and every npm package, a private Python
; 3.12, every Python package as a wheel (the backend, graphify, Scrapling), the bundled Claude skills
; and the settings of the PC the installer was built on (runtime\seed-settings.json, read on the
; first start only). What it fetches while installing, because it cannot be shipped: Claude Code
; (Anthropic's own installer), the Chromium the web tools use, and gltf-transform with npm. Node.js
; through winget, if ticked.
;
; Per user, no administrator: the app writes its data\ folder next to itself, which a folder under
; Program Files would forbid.

#ifndef StageDir
  #error Pass /DStageDir=<the staged app folder> (installer\build_installer.py does this)
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef OutDir
  #define OutDir "Output"
#endif

[Setup]
AppId={{0FFAA6FA-1AC7-4457-ABC0-EBE1FACD8F6F}
AppName=Asset Studio
AppVersion={#AppVersion}
AppVerName=Asset Studio {#AppVersion}
AppPublisher=Asset Studio
DefaultDirName={localappdata}\Programs\Asset Studio
DefaultGroupName=Asset Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=AssetStudio-Setup-{#AppVersion}
SetupIconFile={#StageDir}\frontend\electron\icon.ico
UninstallDisplayIcon={app}\frontend\electron\icon.ico
UninstallDisplayName=Asset Studio
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=yes
; the Python environments, the web tools' Chromium and the code graphs come after the files
ExtraDiskSpaceRequired=1500000000

[Messages]
WelcomeLabel2=This installs Asset Studio and the Studio Engine with everything they need: Python, the app's packages, the code graph (graphify), the web tools (Scrapling) and the settings of the PC this installer was made on.%n%nWhile it installs it downloads Claude Code, a browser for the web tools and gltf-transform (it opens compressed game models), so keep the internet on.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "claude"; Description: "Install Claude Code, the AI agent the Studio runs (Anthropic's installer)"; GroupDescription: "Also install:"
Name: "node"; Description: "Install Node.js LTS with winget (runs web games, opens compressed models)"; GroupDescription: "Also install:"
Name: "codex"; Description: "Install the Codex CLI, a second chat agent (needs Node.js)"; GroupDescription: "Also install:"; Flags: unchecked

[InstallDelete]
; An upgrade replaces these whole, so nothing from an older version stays behind in them.
Type: filesandordirs; Name: "{app}\frontend\dist"
Type: filesandordirs; Name: "{app}\frontend\node_modules"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Asset Studio"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\Asset Studio.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\frontend\electron\icon.ico"
Name: "{autoprograms}\Asset Studio - Setup or repair"; Filename: "{app}\Setup.bat"; WorkingDir: "{app}"; IconFilename: "{app}\frontend\electron\icon.ico"
Name: "{autodesktop}\Asset Studio"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\Asset Studio.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\frontend\electron\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\Asset Studio.vbs"""; Description: "Start Asset Studio"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\stop-studio.ps1"""; Flags: runhidden; RunOnceId: "StopStudio"

[UninstallDelete]
; What setup made after the files were copied. data\ itself stays: settings, history and your work.
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\data\tools"
Type: filesandordirs; Name: "{app}\data\logs"
; the code graph graphify builds of the app's own folder when an agent asks about it
Type: filesandordirs; Name: "{app}\graphify-out"

[Code]
function SetupFlags(): String;
begin
  Result := ' -Yes -SkipUiBuild';
  if not WizardIsTaskSelected('claude') then Result := Result + ' -NoClaude';
  if WizardIsTaskSelected('node') then Result := Result + ' -InstallNode';
  if not WizardIsTaskSelected('codex') then Result := Result + ' -NoCodex';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
  Show: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Setting up Python, graphify, the web tools and Claude Code - a few minutes...';
    Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\setup.ps1') + '"' + SetupFlags();
    if WizardSilent() then Show := SW_HIDE else Show := SW_SHOWNORMAL;
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, ExpandConstant('{app}'),
                Show, ewWaitUntilTerminated, ResultCode) then
      SuppressibleMsgBox('The setup step could not start: ' + SysErrorMessage(ResultCode), mbError, MB_OK, IDOK)
    else if ResultCode <> 0 then
      SuppressibleMsgBox('Part of the setup did not finish (code ' + IntToStr(ResultCode) + ').' + #13#10 + #13#10 +
                         'The log is in ' + ExpandConstant('{app}\data\logs') + '.' + #13#10 +
                         'Run "Asset Studio - Setup or repair" from the Start menu to try again.',
                         mbError, MB_OK, IDOK);
  end;
end;

// setup.ps1 puts the app's bin folder on the user PATH for the graphify command; take it off again.
// (A brace comment cannot say that: the brace of the app constant would end it.)
procedure RemoveFromUserPath(Dir: String);
var
  Paths, Probe: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Paths) then exit;
  Probe := ';' + Uppercase(Paths) + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Probe);
  if P = 0 then exit;
  { In Paths the entry starts at P; the separator before it, if any, is at P - 1. }
  if P > 1 then
    Delete(Paths, P - 1, Length(Dir) + 1)
  else
    Delete(Paths, 1, Length(Dir) + 1);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Paths);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromUserPath(ExpandConstant('{app}\bin'));
end;
