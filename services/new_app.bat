@echo off
REM ============================================================================
REM new_app.bat - Generate app from manifest
REM
REM Usage:
REM   new_app.bat myapp.manifest.yaml
REM   (or drag-drop a manifest file onto this bat)
REM
REM Result:
REM   Creates myapp/ folder with full scaffold
REM ============================================================================

if "%~1"=="" (
    echo.
    echo   Usage: new_app.bat ^<manifest.yaml^>
    echo.
    echo   Example:
    echo     new_app.bat myapp.manifest.yaml
    echo.
    echo   Or drag-drop a manifest file onto this bat file.
    echo.
    pause
    exit /b 1
)

REM Extract app name from manifest filename (without .manifest.yaml)
set "MANIFEST=%~1"
set "APPNAME=%~n1"
set "APPNAME=%APPNAME:.manifest=%"

echo.
echo   Generating %APPNAME% from %MANIFEST%...
echo.

REM Run appctl from shared_libs/tools/appctl
python "%~dp0..\tools\appctl\appctl.py" new "%APPNAME%" --from-manifest "%MANIFEST%" --output "%~dp0%APPNAME%"

if errorlevel 1 (
    echo.
    echo   Error generating app. Check the manifest file.
    pause
    exit /b 1
)

echo.
echo   Done! Your app is ready at: %APPNAME%\
echo.
echo   Next steps:
echo     cd %APPNAME%
echo     copy .env.example .env
echo     REM Edit .env with your secrets
echo     uvicorn %APPNAME%.main:app --reload
echo.
pause
