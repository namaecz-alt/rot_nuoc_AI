@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
.venv\Scripts\python.exe tools\run_pc.py --camera 0
pause
