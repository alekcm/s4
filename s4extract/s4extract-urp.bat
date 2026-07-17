@echo off
setlocal enableextensions enabledelayedexpansion
REM ===================================================================
REM  s4extract launcher for Unity URP projects.
REM  Drag a .package file (or a folder) onto this .bat.
REM
REM  Defaults:
REM    --pipeline urp
REM    --all-lods             (extract all LOD levels)
REM    --no-cas               (skip clothing/hair/body meshes)
REM ===================================================================
cd /d "%~dp0"

set "LOGFILE=%~dp0s4extract_log.txt"

echo ======================================== > "%LOGFILE%"
echo  s4extract URP launcher >> "%LOGFILE%"
echo  Time: %date% %time% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo. >> "%LOGFILE%"

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python not found >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 1
)

echo [1/4] Python found >> "%LOGFILE%"
%PYEXE% --version >> "%LOGFILE%" 2>&1
echo. >> "%LOGFILE%"

if "%~1"=="" (
    echo [ERROR] No file specified >> "%LOGFILE%"
    echo Drag a .package file onto this bat >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 0
)

echo [2/4] Checking dependencies... >> "%LOGFILE%"
echo. >> "%LOGFILE%"

%PYEXE% -c "import numpy" >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m pip install numpy >> "%LOGFILE%" 2>&1
)
%PYEXE% -c "import PIL" >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m pip install Pillow >> "%LOGFILE%" 2>&1
)
%PYEXE% -c "import trimesh" >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m pip install trimesh >> "%LOGFILE%" 2>&1
)
%PYEXE% -c "import scipy" >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m pip install scipy >> "%LOGFILE%" 2>&1
)
%PYEXE% -c "import vhacdx" >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m pip install vhacdx >> "%LOGFILE%" 2>&1
)

echo Dependencies check complete >> "%LOGFILE%"
echo. >> "%LOGFILE%"

echo [3/4] Starting extraction... >> "%LOGFILE%"
echo File: %~1 >> "%LOGFILE%"
echo. >> "%LOGFILE%"

%PYEXE% -m s4extract "%~1" --pipeline urp --all-lods --no-cas >> "%LOGFILE%" 2>&1
set "EXIT_CODE=%errorlevel%"

echo. >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo Finished. Exit code: %EXIT_CODE% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"

type "%LOGFILE%"
echo.
echo ========================================
echo Full log: s4extract_log.txt
echo ========================================
pause
endlocal