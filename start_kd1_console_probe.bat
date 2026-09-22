@echo off
setlocal

cd /d "%~dp0"
title DBS Dahua Access - KD1 console probe

echo DBS Dahua Access - KD1 PIWNICA KOZLA
echo.
echo This window watches TCP connectivity to the Dahua controller.
echo Press Ctrl+C to stop.
echo.

python .\tools\dahua_console_probe.py watch --interval 2

echo.
pause
