@echo off
echo Stopping ALN Data Platform server ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
