@echo off
title screenshot-to-code Dev Server
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

echo.
echo Starting backend (:7001) + frontend (:5173) in THIS window...
echo Press Ctrl+C to stop both servers.
echo.

REM Open the browser once the servers have had a moment to come up.
start /b cmd /c "timeout /t 6 /nobreak >nul && start http://localhost:5173"

REM Single combined console: [backend] blue, [frontend] green (via concurrently).
pnpm dev

echo.
echo Servers stopped.
pause
