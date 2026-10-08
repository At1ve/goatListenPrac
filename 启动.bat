@echo off
cd /d "%~dp0"
python src\app.py
if errorlevel 1 (
  echo.
  echo [!] Start failed. Run: python setup.py
  pause
)