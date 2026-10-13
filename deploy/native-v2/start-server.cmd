@echo off
echo Starting ALN Data Platform server ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
