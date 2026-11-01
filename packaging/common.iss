; Shared by installer.iss (the full installer) and update.iss (the update file): the program's
; identity, so that both install into the same place and keep one uninstaller, and the code that
; closes Hadir before its files are replaced or removed.
#ifndef AppVersion
  #define AppVersion "1.3.1"
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

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0F6D5C-3C1B-4A7E-9F4B-2D8E1A9C7F21}_is1';

{ True while a Hadir.exe is running on this computer. }
function HadirRunning(): Boolean;
var
  Listing: String;
  Output: AnsiString;
  Code: Integer;
begin
  Result := False;
  Listing := AddBackslash(GetTempDir()) + 'hadir-tasks.txt';
  DeleteFile(Listing);
  if Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq Hadir.exe" /NH > "' + Listing + '"', '',
          SW_HIDE, ewWaitUntilTerminated, Code) and LoadStringFromFile(Listing, Output) then
    Result := Pos('hadir.exe', Lowercase(String(Output))) > 0;
  DeleteFile(Listing);
end;

{ A running Hadir keeps its files open, and Windows then refuses to replace or remove them. The
  attendance screen does not close without the administrator's password, so it is ended here.
  The data is safe: every change to the database is a committed SQLite transaction. }
procedure StopHadir();
var
  Code, Waited: Integer;
begin
  if not HadirRunning() then
    exit;
  Log('Hadir is running: ending it before its files change');
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM Hadir.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Waited := 0;
  while HadirRunning() and (Waited < 20) do
  begin
    Sleep(500);
    Waited := Waited + 1;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopHadir();
end;

