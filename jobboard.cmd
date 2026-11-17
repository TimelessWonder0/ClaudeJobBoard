@echo off
rem Double-click or run from any folder: fetches, filters, scores, writes output\jobs_latest.md and the Excel board.
rem Extra switches pass through, e.g.  jobboard.cmd -Open   or   jobboard.cmd -Only Stripe
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
