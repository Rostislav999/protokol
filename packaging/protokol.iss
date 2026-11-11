#ifndef AppVersion
  #define AppVersion "1.7.2"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Protokol"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

[Setup]
AppId={{A71497EF-82C6-4400-AB15-5CBFE75D8D86}
AppName=ОЗтаУСЯ
AppVersion={#AppVersion}
AppPublisher=ОЗтаУСЯ
DefaultDirName={localappdata}\Programs\Protokol
DefaultGroupName=ОЗтаУСЯ
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Protokol-Setup-{#AppVersion}-win64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Protokol.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
SetupIconFile=..\app\static\app.ico
InfoBeforeFile=THIRD_PARTY_NOTICES.md

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ОЗтаУСЯ"; Filename: "{app}\Protokol.exe"; WorkingDir: "{app}"; AppUserModelID: "Protokol.Desktop"
Name: "{autodesktop}\ОЗтаУСЯ"; Filename: "{app}\Protokol.exe"; WorkingDir: "{app}"; AppUserModelID: "Protokol.Desktop"; Tasks: desktopicon

[InstallDelete]
Type: files; Name: "{autoprograms}\Протокол\Протокол.lnk"
Type: dirifempty; Name: "{autoprograms}\Протокол"
Type: files; Name: "{autodesktop}\Протокол.lnk"

[Run]
Filename: "{app}\Protokol.exe"; Description: "Запустить ОЗтаУСЯ"; Flags: nowait postinstall skipifsilent

; User libraries and numbering live outside {app}. Uninstallation deliberately
; leaves %LOCALAPPDATA%\Protokol untouched.
