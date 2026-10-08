@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ============================================================
echo   goatListenPrac 一键构建
echo ============================================================
echo.
echo 将依次完成：
echo   1. 源码自检（确认没有个人数据）
echo   2. 打包成 exe
echo   3. 生成安装包
echo.
echo 需要：Python + pyinstaller + Inno Setup 6
echo 全程约 6 分钟
echo.
pause

cd /d "%~dp0"

echo.
echo ---------- 1/3 源码自检 ----------
python build\build_release.py
if errorlevel 1 (
  echo.
  echo [!] 自检未通过，请先处理上面的问题
  pause
  exit /b 1
)

echo.
echo ---------- 2/3 打包 exe ----------
python build\build_exe.py
if errorlevel 1 (
  echo.
  echo [!] 打包失败
  pause
  exit /b 1
)

echo.
echo ---------- 3/3 生成安装包 ----------
python build\build_installer.py
if errorlevel 1 (
  echo.
  echo [!] 生成安装包失败
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   全部完成，产物在 dist\ 目录下
echo ============================================================
echo.
dir /b dist\*.exe dist\*.zip 2>nul
echo.
pause
