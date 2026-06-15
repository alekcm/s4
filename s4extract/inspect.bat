@echo off
setlocal
REM ===================================================================
REM  Diagnostic: shows what is really inside a .package and saves it to
REM  inspect_report.txt next to this .bat. Drag a .package onto this file.
REM ===================================================================
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python was not found. Install Python 3.9+ first.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Drag a .package file onto this .bat to inspect it.
    pause
    exit /b 0
)

echo Inspecting %~1 ...
%PYEXE% -m s4extract %* --inspect > "%~dp0inspect_report.txt" 2>&1
type "%~dp0inspect_report.txt"
echo.
echo ----------------------------------------------------------
echo Report saved to: %~dp0inspect_report.txt
echo Please send me the contents of that file.
echo ----------------------------------------------------------
pause
endlocal
