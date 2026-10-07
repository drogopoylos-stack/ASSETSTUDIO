@echo off
title Asset Studio - Stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-studio.ps1"
echo.
pause
