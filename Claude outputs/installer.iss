#define MyAppName "BOOM Review"
#define MyAppVersion GetEnv("APP_VERSION")
#define MyAppPublisher "Anh Studio"
#define MyAppExeName "BoomReview.exe"
#define MyAppURL "https://github.com/hoanganhlun186-jpg/boom-review"

[Setup]
AppId={{B00M-REV1EW-A1-V1DEO-RECAP-GEN}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\BoomReview
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=BoomReview-Setup-v{#MyAppVersion}-win64
SetupIconFile=dist\main.dist\assets\boom_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
MinVersion=10.0

[Languages]
Name: "vietnamese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo shortcut trên Desktop"; GroupDescription: "Shortcut:"; Flags: checkedonce

[Files]
; Launcher exe (nằm ngoài thư mục .dist)
Source: "dist\BoomReview.exe"; DestDir: "{app}"; Flags: ignoreversion
; Toàn bộ DLL + assets - giữ đúng tên main.dist (launcher tìm thư mục này)
Source: "dist\main.dist\*"; DestDir: "{app}\main.dist"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Gỡ cài đặt {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Khởi chạy {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\logs"
