; Inno Setup script — builds HadirSetup-<version>.exe
; iscc packaging\installer.iss /DAppVersion=1.0.0
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B0F6D5C-3C1B-4A7E-9F4B-2D8E1A9C7F21}
AppName=حاضر — نظام الحضور ببصمة الوجه
AppVersion={#AppVersion}
AppPublisher=حاضر
DefaultDirName={autopf}\Hadir
DefaultGroupName=حاضر
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=HadirSetup-{#AppVersion}
SetupIconFile=..\hadir\resources\icons\hadir.ico
UninstallDisplayIcon={app}\Hadir.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
MinVersion=10.0
ShowLanguageDialog=no

[Languages]
#if FileExists(CompilerPath + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"
Name: "autostart"; Description: "تشغيل شاشة الحضور تلقائياً عند بدء تشغيل ويندوز (لجهاز الباب)"

[Dirs]
; attendance data is shared by all Windows accounts on the kiosk computer
Name: "{commonappdata}\Hadir"; Permissions: users-modify

[Files]
Source: "..\dist\Hadir\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\حاضر — شاشة الحضور"; Filename: "{app}\Hadir.exe"
Name: "{group}\حاضر — الإدارة"; Filename: "{app}\Hadir.exe"; Parameters: "--admin"
Name: "{group}\حاضر — نسخة تجريبية ببيانات وهمية"; Filename: "{app}\Hadir.exe"; Parameters: "--demo"
Name: "{group}\إزالة حاضر"; Filename: "{uninstallexe}"
Name: "{autodesktop}\حاضر"; Filename: "{app}\Hadir.exe"; Tasks: desktopicon
Name: "{commonstartup}\حاضر"; Filename: "{app}\Hadir.exe"; Tasks: autostart

[Run]
Filename: "{app}\Hadir.exe"; Description: "تشغيل حاضر الآن"; Flags: nowait postinstall skipifsilent

; The attendance database in ProgramData\Hadir is intentionally kept on uninstall.
