; ============================================================
;  goatListenPrac 安装脚本（Inno Setup）
;  由 build_installer.py 调用，不要直接改这里的路径
;
;  设计要点：
;   · 程序目录可选（不只是 C 盘）
;   · 数据目录单独选（音频可能几十 GB，不该塞在程序目录里）
;   · 数据目录路径写进 install.ini，程序启动时读取
; ============================================================

#define AppName        "goatListenPrac"
#define AppNameCN      "精听工具"
#define AppVersion     "1.0.0"
#define AppPublisher   "At1ve"
#define AppURL         "https://github.com/At1ve/goatListenPrac"
#define AppExeName     "goatListenPrac.exe"

[Setup]
AppId={{8F3A2C41-7B9E-4D62-A5C8-1E9F0D3B7A52}
AppName={#AppNameCN}
AppVersion={#AppVersion}
AppVerName={#AppNameCN} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppNameCN}
DisableProgramGroupPage=yes
; 允许用户选程序安装目录（默认仍然给个合理值）
DisableDirPage=no
; 允许用户选数据目录（自定义页，见 [Code]）
DisableReadyPage=no
LicenseFile=__LICENSE__
OutputDir=__OUTDIR__
OutputBaseFilename=__OUTNAME__
SetupIconFile=__ICON__
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; lowest = 不弹 UAC，可装到用户可写的任意位置
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppNameCN}
UninstallDisplayIcon={app}\{#AppExeName}
MinVersion=10.0
ShowLanguageDialog=auto

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
      GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 主程序目录（PyInstaller 产物）
; 排除运行残留：ui_state.json 是用户设置，error.log 是崩溃日志，
; 都不应该跟着安装包分发。
Source: "__BUILDDIR__\*"; DestDir: "{app}"; \
        Flags: ignoreversion recursesubdirs createallsubdirs; \
        Excludes: "ui_state.json,ui_state.json.tmp,error.log,crash.log,\mp3s\*,\transcript\*,\marks\*,\cards\*,\trans_cache\*"

; 附带文档
Source: "__ROOT__\README.md";    DestDir: "{app}\docs"; Flags: ignoreversion
Source: "__ROOT__\使用说明.md";   DestDir: "{app}\docs"; Flags: ignoreversion
Source: "__ROOT__\LICENSE";      DestDir: "{app}";       Flags: ignoreversion

[Dirs]
; 程序目录里的 ffmpeg（setup 时下载，放这里）
Name: "{app}\ffmpeg"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppNameCN}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明";                Filename: "{app}\docs\使用说明.md"
Name: "{group}\打开数据目录";            Filename: "{app}\{#AppExeName}"; \
      Parameters: "--open-data"; Comment: "打开音频与标记所在目录"
Name: "{group}\卸载 {#AppNameCN}";       Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameCN}";     Filename: "{app}\{#AppExeName}"; \
      Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppNameCN}"; \
          Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序文件，绝不碰数据目录
Type: filesandordirs; Name: "{app}\_internal"
Type: files;          Name: "{app}\{#AppExeName}"
Type: filesandordirs; Name: "{app}\docs"

[Code]
var
  DataDirPage: TInputDirWizardPage;
  DataDir: String;

// ---------- 自定义「数据目录」页 ----------
procedure InitializeWizard();
begin
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '选择数据存放位置',
    '音频、转写文字稿和标记要放在哪里？',
    '精听材料（音频文件）可能占用几十 GB 空间，建议放在空间充足的磁盘。' + #13#10 +
    '程序更新或卸载都不会影响这里的数据。',
    False, '');
  DataDirPage.Add('数据目录：');
  // 默认放在「我的文档」下，比 C 盘程序目录更安全
  DataDirPage.Values[0] := ExpandConstant('{userdocs}\goatListenPrac');
end;

function GetDataDir(Param: String): String;
begin
  Result := DataDir;
end;

// 数据目录不能和程序目录相同，否则卸载时容易误删
function NextButtonClick(CurPageID: Integer): Boolean;
var
  d, a: String;
begin
  Result := True;
  if CurPageID = DataDirPage.ID then
  begin
    d := RemoveBackslashUnlessRoot(DataDirPage.Values[0]);
    a := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
    if CompareText(d, a) = 0 then
    begin
      MsgBox('数据目录不能和程序目录相同。' + #13#10 + #13#10 +
             '请换一个位置，例如 D:\goatListenPrac',
             mbError, MB_OK);
      Result := False;
    end;
    DataDir := d;
  end;
end;

// ---------- 建数据目录 ----------
procedure CurStepChanged(CurStep: TSetupStep);
var
  Sub: TArrayOfString;
  i: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Sub := ['mp3s', 'transcript', 'marks', 'cards', 'trans_cache',
            'ffmpeg'];
    for i := 0 to GetArrayLength(Sub) - 1 do
      ForceDirectories(DataDir + '\' + Sub[i]);

    // 把数据目录写进 install.ini，程序启动时读这里
    SetIniString('paths', 'data_dir', DataDir,
                 ExpandConstant('{app}\install.ini'));
  end;
end;

// ---------- 卸载 ----------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  d: String;
  Keep: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // 从 install.ini 读回用户选的数据目录
    d := GetIniString('paths', 'data_dir', '',
                      ExpandConstant('{app}\install.ini'));

    if d <> '' then
    begin
      Keep := MsgBox('是否保留你的学习数据？' + #13#10 + #13#10 +
                     '数据目录：' + #13#10 + d + #13#10 + #13#10 +
                     '选择「是」保留（推荐），「否」删除全部音频和标记。',
                     mbConfirmation, MB_YESNO) = IDYES;
      if not Keep then
        DelTree(d, True, True, True);
    end;

    // 清掉程序目录里可能残留的数据文件夹（旧版本装的）
    DelTree(ExpandConstant('{app}\mp3s'), True, True, True);
    DelTree(ExpandConstant('{app}\marks'), True, True, True);
    DelTree(ExpandConstant('{app}\transcript'), True, True, True);
    DelTree(ExpandConstant('{app}\cards'), True, True, True);
    DelTree(ExpandConstant('{app}\trans_cache'), True, True, True);
    DeleteFile(ExpandConstant('{app}\ui_state.json'));
  end;
end;
