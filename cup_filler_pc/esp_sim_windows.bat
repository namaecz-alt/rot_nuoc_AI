@echo off
rem =====================================================================
rem  Chay ESP32 GIA LAP - thu toan bo luong ma KHONG can phan cung.
rem  Dat coc gia, bam nut gia, xac nhan coc tu PC (nhu camera da nhin thay).
rem =====================================================================
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
.venv\Scripts\python.exe tools\esp_cli.py --port sim --demo
pause
