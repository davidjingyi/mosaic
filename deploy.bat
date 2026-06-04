@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

:: ============================================================
::  Mosaic — One-command deployment script (Windows)
::  Usage: deploy.bat [dev|stop|status]
:: ============================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

set VENV_DIR=venv
set CONFIG_FILE=config.yaml
set EXAMPLE_CONFIG=config.example.yaml
set APP_PID_FILE=.app.pid
set LOG_DIR=logs

:: ── helper ──────────────────────────────────────────────────
:log
    echo [mosaic] %~1
    goto :eof

:warn
    echo [mosaic] WARNING: %~1
    goto :eof

:err
    echo [mosaic] ERROR: %~1
    goto :eof

:: ── check python ────────────────────────────────────────────
:check_python
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        call :err "python not found in PATH"
        exit /b 1
    )
    for /f "tokens=*" %%i in ('python --version 2^>^&1') do call :log "%%i"
    goto :eof

:: ── setup venv ──────────────────────────────────────────────
:setup_venv
    if not exist "%VENV_DIR%\Scripts\python.exe" (
        call :log "Creating virtual environment..."
        python -m venv "%VENV_DIR%"
    ) else (
        call :log "Virtual environment exists: %VENV_DIR%"
    )
    call "%VENV_DIR%\Scripts\activate.bat"
    call :log "Installing dependencies..."
    pip install -q --upgrade pip >nul 2>&1
    pip install -q -r requirements.txt
    call :log "Dependencies installed"
    goto :eof

:: ── config ──────────────────────────────────────────────────
:setup_config
    if not exist "%CONFIG_FILE%" (
        if exist "%EXAMPLE_CONFIG%" (
            copy "%EXAMPLE_CONFIG%" "%CONFIG_FILE%" >nul
            call :warn "Created config.yaml from config.example.yaml"
            call :warn ">>> EDIT config.yaml and set llm.api_key before starting <<<"
        ) else (
            call :err "config.example.yaml not found"
            exit /b 1
        )
    )
    goto :eof

:: ── dirs ────────────────────────────────────────────────────
:setup_dirs
    if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
    if not exist "data" mkdir "data"
    if not exist "knowledge-base" mkdir "knowledge-base"
    goto :eof

:: ── start app ───────────────────────────────────────────────
:start_app
    if exist "%APP_PID_FILE%" (
        set /p PID=<"%APP_PID_FILE%"
        tasklist /fi "PID eq !PID!" 2>nul | find "!PID!" >nul
        if !errorlevel! equ 0 (
            call :log "App already running (PID !PID!)"
            goto :eof
        )
    )

    :: Windows only supports dev mode (uvicorn); gunicorn is Unix-only
    call :log "Starting in DEV mode (uvicorn + auto-reload)..."
    start "Mosaic-App" /B cmd /c ""%VENV_DIR%\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload > "%LOG_DIR%\app.log" 2>&1"
    tasklist /fi "WINDOWTITLE eq Mosaic-App" /fo csv 2>nul | find "Mosaic-App" >nul
    if %errorlevel% equ 0 (
        for /f "tokens=2 delims=," %%i in ('tasklist /fi "WINDOWTITLE eq Mosaic-App" /fo csv 2^>nul ^| find "Mosaic-App"') do (
            set PID=%%~i
            set PID=!PID:"=!
            echo !PID!> "%APP_PID_FILE%"
            call :log "App started (PID !PID!)"
        )
    ) else (
        call :warn "App started in background (PID file not available; check %LOG_DIR%\app.log)"
    )
    goto :eof

:: ── stop ────────────────────────────────────────────────────
:stop_all
    call :log "Stopping services..."

    if exist "%APP_PID_FILE%" (
        set /p PID=<"%APP_PID_FILE%"
        taskkill /PID !PID! /F >nul 2>&1
        del "%APP_PID_FILE%" >nul 2>&1
        call :log "Stopped app (PID !PID!)"
    )

    :: kill any remaining uvicorn processes for this project
    taskkill /FI "WINDOWTITLE eq Mosaic-App" /F >nul 2>&1
    taskkill /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *uvicorn*" /F >nul 2>&1

    call :log "All stopped"
    goto :eof

:: ── status ──────────────────────────────────────────────────
:show_status
    echo.
    echo   Mosaic Services
    echo   ---------------

    if exist "%APP_PID_FILE%" (
        set /p PID=<"%APP_PID_FILE%"
        tasklist /fi "PID eq !PID!" 2>nul | find "!PID!" >nul
        if !errorlevel! equ 0 (
            echo   App        [RUNNING]  (PID !PID!)
        ) else (
            echo   App        [STOPPED]
        )
    ) else (
        echo   App        [STOPPED]
    )

    echo   Embedding  [use external service or modify for local]
    echo.
    goto :eof

:: ── main ────────────────────────────────────────────────────
if "%1"=="" goto :dev
if "%1"=="dev" goto :dev
if "%1"=="stop" goto :stop
if "%1"=="status" goto :status
if "%1"=="restart" goto :restart
goto :help

:dev
    call :check_python
    call :setup_venv
    call :setup_config
    call :setup_dirs
    call :start_app
    call :show_status
    call :log "Deploy complete."
    call :log "Embedding service: start separately with 'python embedding_service.py'"
    call :log "App: http://localhost:8000"
    call :log "Run 'deploy.bat stop' to shut down."
    goto :eof

:stop
    call :stop_all
    call :show_status
    goto :eof

:status
    call :show_status
    goto :eof

:restart
    call :stop_all
    timeout /t 2 /nobreak >nul
    call :check_python
    call :setup_venv
    call :setup_config
    call :setup_dirs
    call :start_app
    call :show_status
    goto :eof

:help
    echo.
    echo   Mosaic — One-command deployment
    echo.
    echo   Usage:
    echo     deploy.bat            Dev mode (uvicorn + auto-reload^)
    echo     deploy.bat stop       Stop all services
    echo     deploy.bat restart    Stop + restart
    echo     deploy.bat status     Show service status
    echo.
    goto :eof

endlocal
