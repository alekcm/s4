@echo off
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python not found!
    pause
    exit /b 1
)

%PYEXE% diagnose.py "%~1" "%~dp0" > diagnose_output.txt 2>&1

type diagnose_output.txt
echo.
echo === Saved to diagnose_output.txt ===
pause
