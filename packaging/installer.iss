; Inno Setup script for Earshot.
; Build the app first:  pyinstaller packaging/meeting_notes.spec
; Then compile this with Inno Setup:  iscc packaging/installer.iss
; Produces a per-user installer (no admin rights needed).

#define MyAppName "Earshot"
#define MyAppVersion "0.5.0"
#define MyAppExe "Earshot.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Whittle
DefaultDirName={localappdata}\Programs\Earshot
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Earshot-Setup-{#MyAppVersion}
SetupIconFile=earshot.ico
UninstallDisplayIcon={app}\Earshot.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Bundle the entire PyInstaller one-folder output.
Source: "..\dist\Earshot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
