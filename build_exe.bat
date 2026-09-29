@echo off
rem === Builds Jarvis.exe (put this file next to jarvis.py, hud.py, jarvis_core.py, jarvis_db.py and requirements.txt) ===
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [1/3] Installing dependencies...
"%PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo [2/3] Building exe (this takes 2-5 minutes)...
"%PY%" -m PyInstaller --noconfirm --clean --noconsole --name Jarvis ^
  --collect-all speech_recognition --collect-all edge_tts --collect-all yt_dlp ^
  --hidden-import pygame --hidden-import pyaudio --hidden-import psutil ^
  --hidden-import jarvis_core --hidden-import jarvis_db --hidden-import hud jarvis.py
if errorlevel 1 goto fail

echo [3/3] Done!
echo.
echo   Your program:  %~dp0dist\Jarvis\Jarvis.exe
echo   (keep the whole "Jarvis" folder together, do not move only the exe)
echo   Log file (if something goes wrong): dist\Jarvis\jarvis.log
echo.
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - copy the error text above and send it to Claude.
pause
exit /b 1