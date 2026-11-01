; Inno Setup script — builds HadirSetup-<version>.exe, the full installer
; iscc packaging\installer.iss /DAppVersion=1.3.1
#include "common.iss"

[Setup]
OutputBaseFilename=HadirSetup-{#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"
Name: "autostart"; Description: "تشغيل شاشة الحضور تلقائياً عند بدء تشغيل ويندوز (لجهاز الباب)"

[Dirs]
; attendance data is shared by all Windows accounts on the kiosk computer
Name: "{commonappdata}\Hadir"; Permissions: users-modify

[InstallDelete]
; a clean copy of the program's libraries: nothing left of an earlier version or of a failed copy
; (the data is in ProgramData\Hadir, not here)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; dist\Hadir holds hadir-files.txt too: the SHA-256 of every file, as built
Source: "..\dist\Hadir\*"; DestDir: "{app}"; Excludes: "portable.txt"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\حاضر — شاشة الحضور"; Filename: "{app}\Hadir.exe"
Name: "{group}\حاضر — الإدارة"; Filename: "{app}\Hadir.exe"; Parameters: "--admin"
Name: "{group}\حاضر — نسخة تجريبية ببيانات وهمية"; Filename: "{app}\Hadir.exe"; Parameters: "--demo"
Name: "{group}\إزالة حاضر"; Filename: "{uninstallexe}"
Name: "{autodesktop}\حاضر"; Filename: "{app}\Hadir.exe"; Tasks: desktopicon
Name: "{commonstartup}\حاضر"; Filename: "{app}\Hadir.exe"; Tasks: autostart

[Run]
Filename: "{app}\Hadir.exe"; Description: "تشغيل حاضر الآن"; Flags: nowait postinstall skipifsilent runasoriginaluser

; The attendance database in ProgramData\Hadir is intentionally kept on uninstall.

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopHadir();
  Result := '';
end;
