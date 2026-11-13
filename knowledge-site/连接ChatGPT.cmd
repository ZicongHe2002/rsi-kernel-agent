@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect-chatgpt.ps1"
set "connectExitCode=%errorlevel%"
if not "%connectExitCode%"=="0" pause
exit /b %connectExitCode%
