@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Polyglass is not installed yet. Run Install.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0polyglass.py"
