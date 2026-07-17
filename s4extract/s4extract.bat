@echo off
setlocal enableextensions enabledelayedexpansion
REM ===================================================================
REM  s4extract launcher (Built-in pipeline).
REM  Drag a .package file (or a folder) onto this .bat.
REM
REM  Defaults:
REM    --pipeline builtin
REM    --all-lods             (extract all LOD levels)
REM    --no-cas               (skip clothing/hair/body meshes)
REM ===================================================================
cd /d "%~dp0"

set "LOGFILE=%~dp0s4extract_log.txt"

echo ======================================== > "%LOGFILE%"
echo  s4extract launcher (Built-in) >> "%LOGFILE%"
echo  Time: %date% %time% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM --- Find Python ---
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python not found >> "%LOGFILE%"
    echo Python not found! Install Python 3.9+ from python.org >> "%LOGFILE%"
    echo Do not forget to check "Add Python to PATH" >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 1
)

echo [1/4] Python found >> "%LOGFILE%"
%PYEXE% --version >> "%LOGFILE%" 2>&1
echo. >> "%LOGFILE%"

REM --- Check input ---
if "%~1"=="" (
    echo [ERROR] No file specified >> "%LOGFILE%"
    echo Drag a .package file onto this bat >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 0
)

echo [2/4] Checking dependencies... >> "%LOGFILE%"

%PYEXE% -c "import numpy; import PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing numpy and Pillow... >> "%LOGFILE%"
    %PYEXE% -m pip install numpy Pillow >> "%LOGFILE%" 2>&1
)

%PYEXE% -c "import trimesh; import scipy" >nul 2>&1
if errorlevel 1 (
    echo Installing trimesh and scipy... >> "%LOGFILE%"
    %PYEXE% -m pip install trimesh scipy >> "%LOGFILE%" 2>&1
)

%PYEXE% -c "import vhacdx" >nul 2>&1
if errorlevel 1 (
    echo Installing vhacdx (may fail - this is OK)... >> "%LOGFILE%"
    %PYEXE% -m pip install vhacdx >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [WARNING] vhacdx failed - colliders will be limited >> "%LOGFILE%"
    )
)

echo. >> "%LOGFILE%"

REM --- Run extraction ---
echo [3/4] Starting extraction... >> "%LOGFILE%"
echo File: %~1 >> "%LOGFILE%"
echo Command: %PYEXE% -m s4extract "%~1" --pipeline builtin --all-lods --no-cas >> "%LOGFILE%"
echo. >> "%LOGFILE%"

%PYEXE% -m s4extract "%~1" --pipeline builtin --all-lods --no-cas >> "%LOGFILE%" 2>&1
set "EXIT_CODE=%errorlevel%"

echo. >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo Finished. Exit code: %EXIT_CODE% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"

REM --- Show results ---
type "%LOGFILE%"
echo.
echo ========================================
echo Full log: s4extract_log.txt
echo ========================================
pause
endlocal