@echo off
setlocal

cd /d "%~dp0"
title DBS Dahua Access - KD1 NetSDK event listener

echo DBS Dahua Access - KD1 PIWNICA KOZLA
echo.
echo NetSDK listener will log in to the controller and print access events.
echo Use a card or PIN at the reader, then watch this window.
echo Press Ctrl+C to stop.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Run setup first or ask Codex to recreate it.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -u ".\tools\dahua_netsdk_console.py" listen

echo.
pause
