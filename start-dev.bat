@echo off
title screenshot-to-code Dev Launcher
echo.
echo ========================================
echo   screenshot-to-code Dev Server
echo ========================================
echo.

cd /d "%~dp0"

REM --- Clean up leftover dev processes from previous runs -------------------
REM Kill whatever holds our dev ports so uvicorn never hits WinError 10013
REM and Vite never silently drifts to 5174/5175/...
set "PORTS=7001 5173"
for %%P in (%PORTS%) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
        echo Killing stale process on port %%P ^(PID %%A^)
        taskkill /F /PID %%A >nul 2>&1
    )
)
REM Also kill stray Vite processes from previous runs of this app (any port).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -like '*screenshot-to-code-with-all-providers*frontend*vite*' } | ForEach-Object { Write-Host ('Killing stale Vite PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [1/3] Starting backend on port 7001...
start "backend (uvicorn :7001)" cmd /k "cd /d %~dp0backend && python -m uvicorn main:app --reload --port 7001"

timeout /t 2 /nobreak >nul

echo [2/3] Starting frontend on port 5173 (strict)...
start "frontend (vite :5173)" cmd /k "cd /d %~dp0frontend && pnpm dev --port 5173 --strictPort"

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
