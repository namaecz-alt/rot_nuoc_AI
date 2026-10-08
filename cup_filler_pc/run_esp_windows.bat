@echo off
rem =====================================================================
rem  Chay CA HE THONG voi ESP32 THAT qua cong COM.
rem  Dat coc vao khay -> may tinh tu bat camera -> nhan dien -> mo khoa nut
rem  -> bam nut tren ESP32 -> bom nuoc.
rem =====================================================================
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Hay chay install_windows.bat truoc.
  pause
  exit /b 1
)
set /p COMPORT= Nhap cong COM cua ESP32 (vi du COM5): 
if "%COMPORT%"=="" set COMPORT=COM5
echo.
echo Dang ket noi %COMPORT% ... (nhan Ctrl+C de dung)
.venv\Scripts\python.exe tools\run_pc.py --port %COMPORT%
pause
