@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-website.ps1"
if errorlevel 1 (
  echo.
  echo Wattwise could not start. Read the error above, then press any key to close this window.
  pause >nul
)
