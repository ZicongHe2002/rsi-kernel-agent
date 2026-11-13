@echo off
setlocal
if exist "%USERPROFILE%\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe" (
    "%USERPROFILE%\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe" "%~dp0launch.py" %*
    goto done
)
where python.exe >nul 2>nul
if not errorlevel 1 (
    python.exe -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
    if not errorlevel 1 (
        python.exe "%~dp0launch.py" %*
        goto done
    )
)
where py.exe >nul 2>nul
if not errorlevel 1 (
    py.exe -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul
    if not errorlevel 1 (
        py.exe -3 "%~dp0launch.py" %*
        goto done
    )
)
echo Python 3.10 or newer is required. Please install Python and try again.
pause
exit /b 1
:done
set "siteExitCode=%errorlevel%"
if not "%siteExitCode%"=="0" pause
exit /b %siteExitCode%
