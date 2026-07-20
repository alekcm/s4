@echo off
setlocal enableextensions
REM ===================================================================
REM  Cross-package material diagnostic for The Sims 4 FullBuild files.
REM
REM  Drag any ClientFullBuild0/1/2.package (or FullBuild0/1/2.package)
REM  onto this file. It finds numbered sibling packs in the same folder
REM  and writes diagnose_linked_packs_output.txt next to this .bat.
REM ===================================================================
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python not found. Install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Drag a numbered FullBuild .package onto this .bat file.
    pause
    exit /b 1
)

set "OUT=%~dp0diagnose_linked_packs_output.txt"
%PYEXE% diagnose_linked_packs.py "%~1" > "%OUT%" 2>&1
set "CODE=%ERRORLEVEL%"

type "%OUT%"
echo.
echo ================================================================
echo Saved to: %OUT%
echo Exit code: %CODE%
echo ================================================================
pause
exit /b %CODE%
