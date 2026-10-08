@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
.venv\Scripts\python.exe tools\train_yolo.py --epochs 30 --imgsz 480 --batch 4 --device cpu
pause
