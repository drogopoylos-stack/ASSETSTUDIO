@echo off
title Asset Studio - Update
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-studio.ps1"
echo.
pause
