@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title DBS Dahua Access - Web Lab

if exist "secrets\kd1_piwnica_kozla.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("secrets\kd1_piwnica_kozla.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

if exist "secrets\cam_10_10_20_52.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("secrets\cam_10_10_20_52.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

if exist "secrets\nvr_10_10_20_20.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("secrets\nvr_10_10_20_20.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

if not defined DAHUA_HOST (
    echo Missing DAHUA_* environment variables.
    echo Set them in your shell or keep secrets\kd1_piwnica_kozla.env locally.
    pause
    exit /b 1
)

set "DAHUA_DEFAULT_DOOR=2"
set "DAHUA_DOORS=1,2,3,4"
set "DBS_WEB_HOST=127.0.0.1"
set "DBS_WEB_PORT=8787"

if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv. Ask Codex to recreate the local SDK environment.
    pause
    exit /b 1
)

echo Starting DBS Dahua Access Web Lab on http://%DBS_WEB_HOST%:%DBS_WEB_PORT%/
echo.

".venv\Scripts\python.exe" -u ".\tools\dahua_web_app.py" --host "%DBS_WEB_HOST%" --port "%DBS_WEB_PORT%" --open-browser

echo.
pause
