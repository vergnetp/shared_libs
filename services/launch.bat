@echo off
REM ================================
REM Dev startup script (Windows)
REM ================================
REM
REM Structure:
REM   shared_libs/
REM   ├── backend/
REM   └── services/
REM       ├── launch.bat     (this file)
REM       └── ai_agents/
REM
REM Usage:
REM   1. Double-click: Launches ai_agents
REM   2. Drag manifest onto this: Launches that service
REM ================================

echo.
echo ================================
echo Agent Service Launcher (DEV)
echo ================================
echo.

REM ---- SET PATHS ----
REM services/ folder (where this script lives)
set "SERVICES_DIR=%~dp0"
if "%SERVICES_DIR:~-1%"=="\" set "SERVICES_DIR=%SERVICES_DIR:~0,-1%"

REM shared_libs/ folder (parent of services/)
for %%i in ("%SERVICES_DIR%") do set "SHARED_LIBS=%%~dpi"
if "%SHARED_LIBS:~-1%"=="\" set "SHARED_LIBS=%SHARED_LIBS:~0,-1%"

REM ---- DETECT SERVICE ----
set "SERVICE_NAME=ai_agents"

if "%~1" NEQ "" (
    echo Manifest provided: %~1
    if /i "%~nx1"=="manifest.yaml" (
        for %%i in ("%~dp1.") do set "SERVICE_NAME=%%~ni"
    ) else (
        for /f "tokens=1 delims=." %%a in ("%~n1") do set "SERVICE_NAME=%%a"
    )
)

echo Service: %SERVICE_NAME%
echo Shared libs: %SHARED_LIBS%
echo.

REM ---- ENV VARS ----
set "REDIS_URL=redis://localhost:6379/0"
set "JWT_SECRET=dev-secret-change-in-prod"
set "PYTHONUNBUFFERED=1"

echo REDIS_URL=%REDIS_URL%
echo.

REM ---- START REDIS (Docker) ----
echo Checking Redis...
docker ps --filter "name=redis" --format "{{.Names}}" 2>nul | findstr redis >nul
if %ERRORLEVEL% NEQ 0 (
    docker ps -a --filter "name=redis" --format "{{.Names}}" 2>nul | findstr redis >nul
    if %ERRORLEVEL% EQU 0 (
        docker start redis
    ) else (
        docker run -d --name redis -p 6379:6379 redis:7
    )
) else (
    echo Redis running.
)

echo.
timeout /t 2 >nul

REM ---- START API ----
echo Starting API...
start "%SERVICE_NAME%_api" cmd /k "cd /d %SHARED_LIBS% && uvicorn services.%SERVICE_NAME%.main:app --reload --host 0.0.0.0 --port 8000"

timeout /t 2 >nul

REM ---- START WORKER ----
echo Starting Worker...
start "%SERVICE_NAME%_worker" cmd /k "cd /d %SHARED_LIBS% && python -m services.%SERVICE_NAME%.worker"

echo.
echo ================================
echo Started: %SERVICE_NAME%
echo ================================
echo API:   http://localhost:8000
echo Docs:  http://localhost:8000/docs
echo.

pause