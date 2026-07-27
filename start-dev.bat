@echo off
title screenshot-to-code Dev Launcher
echo.
echo ========================================
echo   screenshot-to-code Dev Server
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] Starting backend on port 7001...
start "backend (uvicorn :7001)" cmd /k "cd /d %~dp0backend && python -m uvicorn main:app --reload --port 7001"

timeout /t 2 /nobreak >nul

echo [2/3] Starting frontend on port 5173...
start "frontend (vite :5173)" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo [3/3] Waiting for servers...
timeout /t 5 /nobreak >nul

echo.
echo ========================================
echo   Open browser: http://localhost:5173
echo ========================================
echo.
echo Press any key to open browser now...
pause >nul

start http://localhost:5173

echo.
echo Servers are running in separate windows.
echo Close those windows to stop the servers.
pause
