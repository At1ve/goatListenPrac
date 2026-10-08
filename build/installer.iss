; ============================================================
;  goatListenPrac 安装脚本（Inno Setup）
;  由 build_installer.py 调用，不要直接改这里的路径
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
LicenseFile=LICENSE
OutputDir=__OUTDIR__
OutputBaseFilename=__OUTNAME__
SetupIconFile=__ICON__
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppNameCN}
UninstallDisplayIcon={app}\{#AppExeName}
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
      GroupDescription: "附加任务:"; Flags: checkedonce
Name: "quicklaunchicon"; Description: "创建快速启动栏图标"; \
      GroupDescription: "附加任务:"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
; 主程序目录（PyInstaller 产物）
; 排除运行残留：ui_state.json 是用户设置，error.log 是崩溃日志，
; 都不应该跟着安装包分发。
Source: "__BUILDDIR__\*"; DestDir: "{app}"; \
        Flags: ignoreversion recursesubdirs createallsubdirs; \
        Excludes: "ui_state.json,ui_state.json.tmp,error.log,crash.log,\mp3s\*,\transcript\*,\marks\*,\cards\*,\trans_cache\*"

; 附带文档
Source: "README.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "使用说明.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; 用户数据目录（卸载时保留）
Name: "{app}\mp3s";          Permissions: users-modify
Name: "{app}\transcript";    Permissions: users-modify
Name: "{app}\marks";         Permissions: users-modify
Name: "{app}\cards";         Permissions: users-modify
Name: "{app}\trans_cache";   Permissions: users-modify
Name: "{app}\ffmpeg";        Permissions: users-modify

[Icons]
Name: "{group}\{#AppNameCN}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明";                Filename: "{app}\docs\使用说明.md"
Name: "{group}\卸载 {#AppNameCN}";       Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameCN}";     Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppNameCN}"; \
          Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序文件，保留用户数据
Type: filesandordirs; Name: "{app}\_internal"
Type: files;          Name: "{app}\{#AppExeName}"
Type: filesandordirs; Name: "{app}\docs"

[Code]
// 卸载时询问是否保留用户数据
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDirs: TArrayOfString;
  i: Integer;
  Keep: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Keep := MsgBox('是否保留你的音频和标记数据？' + #13#10 + #13#10 +
                   '选择「是」保留 mp3s / marks / transcript 目录，' + #13#10 +
                   '选择「否」全部删除。',
                   mbConfirmation, MB_YESNO) = IDYES;
    if not Keep then
    begin
      DataDirs := [
        ExpandConstant('{app}\mp3s'),
        ExpandConstant('{app}\marks'),
        ExpandConstant('{app}\transcript'),
        ExpandConstant('{app}\cards'),
        ExpandConstant('{app}\trans_cache'),
        ExpandConstant('{app}\ffmpeg')
      ];
      for i := 0 to GetArrayLength(DataDirs) - 1 do
        DelTree(DataDirs[i], True, True, True);
      DeleteFile(ExpandConstant('{app}\ui_state.json'));
    end;
  end;
end;
