@echo off
setlocal
cd /d "%~dp0"
title Protokol

py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python313
py -3.12 "%~dp0launch.py" %*
goto finished

:try_python313
py -3.13 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
py -3.13 "%~dp0launch.py" %*
goto finished

:try_python
python -c "import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] < (3,14) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0launch.py" %*
goto finished

:missing_python
echo.
echo Python 3.12 or 3.13 was not found.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then run START_WINDOWS.bat again.
echo.
pause
exit /b 1

:finished
set "PROTOKOL_LAUNCH_EXIT=%errorlevel%"
if "%PROTOKOL_LAUNCH_EXIT%"=="0" exit /b 0
echo.
echo The program could not start. Please read the message above.
pause
exit /b %PROTOKOL_LAUNCH_EXIT%
