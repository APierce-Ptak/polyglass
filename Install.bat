@echo off
setlocal
cd /d "%~dp0"

set PYV=
for %%V in (3.12 3.11 3.10) do (
  if not defined PYV (
    py -%%V -c "import tkinter" >nul 2>nul && set PYV=%%V
  )
)

if not defined PYV (
  echo Python 3.10-3.12 with Tk was not found.
  where winget >nul 2>nul
  if errorlevel 1 (
    echo Please install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
    echo then double-click Install.bat again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Python was installed. Please double-click Install.bat again to continue.
  pause
  exit /b 0
)

start "" pyw -%PYV% "%~dp0setup_wizard.py"
