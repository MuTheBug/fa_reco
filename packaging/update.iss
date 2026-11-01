; Inno Setup script — builds HadirUpdate-<version>.exe, the update file. It carries only the files
; that changed since the earlier releases; packaging\update_files.py prepares them in build\update:
;   python packaging\update_files.py plan dist\Hadir build\bases build\update
;   iscc packaging\update.iss /DAppVersion=1.3.1
; Before copying anything it checks that every other file of the installed program is exactly the
; one this version was built with, so a computer it cannot bring to this version stays as it was.
#include "common.iss"
#include "..\build\update\info.iss"

[Setup]
OutputBaseFilename=HadirUpdate-{#AppVersion}
; the program is updated where it is installed; its shortcuts stay as they are, and the
; uninstaller (same AppId) also removes what the update brings
UsePreviousAppDir=yes
DisableDirPage=yes
DisableReadyPage=no

[Messages]
SetupAppTitle=تحديث حاضر
SetupWindowTitle=تحديث حاضر إلى الإصدار {#AppVersion}
WizardReady=تحديث حاضر إلى الإصدار {#AppVersion}
ReadyLabel1=يحدّث هذا الملف حاضر المثبّت على هذا الجهاز إلى الإصدار {#AppVersion} بنسخ الملفات التي تغيّرت فقط، وهو يحدّث {#UpdateFromText}.
ReadyLabel2b=يتأكد أولاً من سلامة باقي ملفات البرنامج، ثم يُغلق حاضر إن كان يعمل وينسخ الملفات الجديدة. البيانات تبقى كما هي. اضغط «تثبيت» للمتابعة.
FinishedHeadingLabel=اكتمل التحديث
FinishedLabelNoIcons=صار حاضر على هذا الجهاز بالإصدار {#AppVersion}.
FinishedLabel=صار حاضر على هذا الجهاز بالإصدار {#AppVersion}.

[Files]
Source: "..\build\update\payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\build\update\check.txt"; Flags: dontcopy

#include "..\build\update\delete.iss"

[Run]
Filename: "{app}\Hadir.exe"; Description: "تشغيل حاضر الآن"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  CheckPage: TOutputProgressWizardPage;
  Checked: Boolean;
  Problem: String;

function InstalledVersion(): String;
var
  Version: String;
begin
  if RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Version) then
    Result := Version
  else
    Result := 'غير معروف';
end;

procedure InitializeWizard();
begin
  CheckPage := CreateOutputProgressPage('فحص البرنامج المثبّت',
    'يتأكد التحديث من سلامة ملفات حاضر على هذا الجهاز قبل أن ينسخ شيئاً.');
end;

{ The files the update leaves in place must be exactly those of this version: check.txt lists
  their SHA-256, one "<sha256> <path>" per line (written by update_files.py). }
function CheckFiles(): String;
var
  Lines: TArrayOfString;
  I, Count, Bad: Integer;
  Rel, Path, Got, Listed: String;
begin
  Result := '';
  ExtractTemporaryFile('check.txt');
  if not LoadStringsFromFile(ExpandConstant('{tmp}\check.txt'), Lines) then
  begin
    Result := 'تعذّرت قراءة قائمة الملفات داخل ملف التحديث، فقد يكون ملف التحديث نفسه تالفاً. نزّله من جديد.';
    exit;
  end;
  Count := GetArrayLength(Lines);
  Bad := 0;
  Listed := '';
  CheckPage.SetText('جارٍ فحص ملفات البرنامج المثبّت…', '');
  CheckPage.SetProgress(0, Count);
  CheckPage.Show;
  try
    for I := 0 to Count - 1 do
    begin
      Rel := Copy(Lines[I], 66, Length(Lines[I]) - 65);
      Path := ExpandConstant('{app}\') + Rel;
      CheckPage.SetText('جارٍ فحص ملفات البرنامج المثبّت…', Rel);
      Got := '';
      if FileExists(Path) then
        try
          Got := GetSHA256OfFile(Path);
        except
          Got := '';
        end;
      if CompareText(Got, Copy(Lines[I], 1, 64)) <> 0 then
      begin
        Bad := Bad + 1;
        Log('missing or different: ' + Rel);
        if Bad <= 10 then
          Listed := Listed + #13#10 + Rel;
      end;
      CheckPage.SetProgress(I + 1, Count);
    end;
  finally
    CheckPage.Hide;
  end;
  Log(Format('checked %d files in place: %d missing or different', [Count, Bad]));
  if Bad > 10 then
    Listed := Listed + #13#10 + '…';
  if Bad > 0 then
    Result := Format('لا يكفي ملف التحديث لهذا الجهاز: بعض ملفات حاضر المثبّت ناقصة أو تالفة أو من إصدار آخر، ' +
      'وهي ليست في ملف التحديث (عددها %d):%s' + #13#10#13#10 +
      'الإصدار المثبّت: %s، وهذا التحديث يحدّث {#UpdateFromText}.' + #13#10 +
      'لم يتغيّر شيء على الجهاز. ثبّت ملف التثبيت الكامل HadirSetup-{#AppVersion}.exe مرة واحدة ' +
      '(يستبدل كل الملفات، والبيانات تبقى كما هي)، وبعدها تكفي ملفات التحديث.', [Bad, Listed, InstalledVersion()]);
end;

function RunChecks(): String;
begin
  Checked := True;
  if not FileExists(ExpandConstant('{app}\Hadir.exe')) then
    Result := 'لم يُعثر على حاضر مثبّتاً على هذا الجهاز. ملف التحديث يحدّث برنامجاً مثبّتاً فقط، ' +
      'فثبّت أولاً ملف التثبيت الكامل HadirSetup-{#AppVersion}.exe.'
  else
    Result := CheckFiles();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then
    Problem := RunChecks();
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  if not Checked then
    Problem := RunChecks();
  Result := Problem;
  if Result = '' then
    StopHadir();
end;
