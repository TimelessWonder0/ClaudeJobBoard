@echo off
rem Double-click or run from any folder: scans, builds the Excel board, opens it.
rem Extra switches pass through, e.g.  jobboard.cmd -Review   or   jobboard.cmd -NoOpen
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0jobboard.ps1" %*
set JB_EXIT=%ERRORLEVEL%

rem Keep the window open when launched by double-click (cmd /c), so the summary can be read.
echo %CMDCMDLINE% | find /i "/c" >nul
if not errorlevel 1 (
    echo.
    echo jobboard finished with exit code %JB_EXIT%. Log: %~dp0logs
    pause
)
exit /b %JB_EXIT%
