; NexusBI Enterprise - Standalone Windows x64 Installer Script (Inno Setup 6)
; Produces a single offline setup.exe requiring zero external user prerequisites.

#define MyAppName "NexusBI Enterprise"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "NexusBI Contributors"
#define MyAppURL "https://github.com/aftabcollege-gif/Dashboard"
#define MyAppExeName "NexusBI.exe"

[Setup]
AppId={{8D6B4F7A-91C2-4E47-9D15-7B4E23D1B019}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} (x64)
VersionInfoVersion={#MyAppVersion}.0
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\NexusBI
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=License.txt
InfoAfterFile=PostInstallNotes.txt
OutputDir=..\dist
OutputBaseFilename=setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
UninstallDisplayIcon={app}\{#MyAppExeName}
AppMutex=Local\NexusBI_Desktop_Instance
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Native supervisor launcher
Source: "..\stage\NexusBI.exe"; DestDir: "{app}"; Flags: ignoreversion

; Bundled Python 3.11 x64 runtime + pre-installed site-packages
Source: "..\stage\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; Bundled Node.js x64 binary + runtime gateway
Source: "..\stage\runtime\node\*"; DestDir: "{app}\runtime\node"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\stage\runtime\web_gateway.mjs"; DestDir: "{app}\runtime"; Flags: ignoreversion

; FastAPI backend application source (no dev data or .venv)
Source: "..\stage\backend\*"; DestDir: "{app}\backend"; Flags: ignoreversion recursesubdirs createallsubdirs

; Next.js standalone production build + static assets
Source: "..\stage\frontend\*"; DestDir: "{app}\frontend"; Flags: ignoreversion recursesubdirs createallsubdirs

; Optional Microsoft VC++ x64 Redistributable (installed silently only if admin & missing)
Source: "..\stage\prereqs\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist; Check: ShouldInstallVcRedist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\NexusBI Data Folder"; Filename: "{localappdata}\NexusBI\data"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Microsoft Visual C++ Runtime..."; Flags: waituntilterminated skipifdoesntexist; Check: ShouldInstallVcRedist
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopNexusBIServices"

[Code]
function ShouldInstallVcRedist: Boolean;
var
  Installed: Cardinal;
begin
  Result := False;
  if not IsAdminInstallMode then
    Exit;
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
  begin
    Result := (Installed <> 1);
  end
  else
  begin
    Result := True;
  end;
end;

procedure StopExistingInstanceIfPresent(ExePath: string);
var
  ResultCode: Integer;
begin
  if FileExists(ExePath) then
  begin
    Exec(ExePath, '--stop', ExtractFileDir(ExePath), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopExistingInstanceIfPresent(ExpandConstant('{app}\{#MyAppExeName}'));
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopExistingInstanceIfPresent(ExpandConstant('{app}\{#MyAppExeName}'));
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\NexusBI');
    if DirExists(DataDir) and not UninstallSilent then
    begin
      MsgBox(
        'NexusBI application files were removed.' + #13#10 + #13#10 +
        'Your databases, dashboards, uploads, and settings were preserved at:' + #13#10 +
        DataDir + #13#10 + #13#10 +
        'You may back up or manually delete that folder at any time.',
        mbInformation, MB_OK
      );
    end;
  end;
end;
