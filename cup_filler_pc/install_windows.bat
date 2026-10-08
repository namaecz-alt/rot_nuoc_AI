@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Khong tim thay Python Launcher 'py'. Cai Python 3.10-3.13 tu python.org va chon Add Python to PATH.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements_pc.txt
if errorlevel 1 (
  echo Cai thu vien that bai. Hay kiem tra mang va phien ban Python.
  pause
  exit /b 1
)
echo.
echo Cai xong. Chay run_windows.bat de mo webcam.
pause
