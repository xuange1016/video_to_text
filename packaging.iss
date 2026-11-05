[Setup]
AppId={{BCC6455C-0B16-4424-8622-783F0F96C6C0}
AppName=片语
AppVersion={#AppVersion}
AppPublisher=片语
DefaultDirName={localappdata}\Programs\Pianyu
DefaultGroupName=片语
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=dist
OutputBaseFilename=Pianyu-{#AppVersion}-win-x64-Setup
SetupIconFile=.build\pianyu.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Pianyu.exe
CloseApplications=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "startmenu"; Description: "创建开始菜单快捷方式"

[Files]
Source: ".build\release\{#AppVersion}\Pianyu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\片语"; Filename: "{app}\Pianyu.exe"; WorkingDir: "{app}"; Tasks: startmenu

[Run]
Filename: "{app}\Pianyu.exe"; Description: "启动片语"; Flags: nowait postinstall skipifsilent
