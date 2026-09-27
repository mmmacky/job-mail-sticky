@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 "%~dp0sticky.py"
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python -X utf8 "%~dp0sticky.py"
  goto finished
)
echo Python not found. Install Python 3 with Tcl/Tk and the Python launcher.
:finished
echo.
echo The app has closed. If startup failed, review the error above.
pause
