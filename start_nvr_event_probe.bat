@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title DBS Dahua NVR Event Probe

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Ask Codex to recreate the local SDK environment.
    pause
    exit /b 1
)

if not exist "secrets\nvr_10_10_20_20.env" (
    echo Missing secrets\nvr_10_10_20_20.env
    pause
    exit /b 1
)

echo Running passive NVR probe first...
".venv\Scripts\python.exe" -u ".\tools\dahua_nvr_event_probe.py" --env-file "secrets\nvr_10_10_20_20.env"
echo.
echo Running active NVR event tests...
".venv\Scripts\python.exe" -u ".\tools\dahua_nvr_event_probe.py" --env-file "secrets\nvr_10_10_20_20.env" --active --wait 10

echo.
pause
