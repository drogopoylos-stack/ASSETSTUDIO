@echo off
title Asset Studio - Restart Backend
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-backend.ps1"
echo.
pause
