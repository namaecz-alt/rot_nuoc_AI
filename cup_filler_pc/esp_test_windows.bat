@echo off
rem =====================================================================
rem  Kiem tra nhanh bo ESP32: bat tay UART, dat coc, mo khoa, bom 200 ml.
rem  Chay duoc ca khi CHUA co camere va ca khi chua co phan cung (--port sim).
rem =====================================================================
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
set /p COMPORT= Nhap cong COM cua ESP32 (bo trong = dung ESP gia lap): 
if "%COMPORT%"=="" set COMPORT=sim
.venv\Scripts\python.exe tools\esp_cli.py --port %COMPORT% --demo
pause
