#define MyAppName "电子税务局企业登录助手"
#define MyAppVersion "2.0.0"
#define MyAppExeName "电子税务局企业登录助手.exe"

[Setup]
AppId={{A41B9342-98D6-4F29-8492-27FB3B4A4D77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=xArsenn
AppPublisherURL=https://github.com/xArsenn/e-tax-login-assistant
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=installer-dist
OutputBaseFilename=电子税务局企业登录助手-安装程序
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
