@echo off
rem Runs Polyglass with a console window so you can see what it is doing.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Polyglass is not installed yet. Run Install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" polyglass.py
pause
