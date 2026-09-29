@echo off
rem === Adds Jarvis.exe to Windows startup (run AFTER build_exe.bat) ===
set EXE=%~dp0dist\Jarvis\Jarvis.exe
if not exist "%EXE%" (
  echo Jarvis.exe not found. Run build_exe.bat first.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Jarvis.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%~dp0dist\Jarvis'; $s.Arguments='--minimized'; $s.Save()"
echo Done. Jarvis will start automatically when you log in to Windows.
echo To remove: press Win+R, type  shell:startup  and delete the Jarvis shortcut.
pause