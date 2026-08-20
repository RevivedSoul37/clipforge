@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title ClipForge

echo.
echo  ============================================================
echo    ClipForge - AI auto-clipper
echo  ============================================================
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo  [ERROR] ffmpeg was not found on your PATH.
    echo          Install it from https://www.gyan.dev/ffmpeg/builds/
    echo          and make sure "ffmpeg" runs from any terminal.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo  [ERROR] Virtual environment ".venv" was not found.
    echo          Recreate it with:  python -m venv .venv
    echo          then:              .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

set "PORT=8600"

rem ---- free the port if a stale ClipForge server holds it -----------------
netstat -ano | findstr /R /C:":%PORT%  *[^ ]*  *[^ ]*  *LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo  Port %PORT% is already in use - a previous ClipForge server
    echo  is probably still running. Closing it so you get the latest code...
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT%  *[^ ]*  *[^ ]*  *LISTENING"') do (
        taskkill /F /T /PID %%P >nul 2>nul
    )
    timeout /t 2 >nul
    netstat -ano | findstr /R /C:":%PORT%  *[^ ]*  *[^ ]*  *LISTENING" >nul 2>nul
    if not errorlevel 1 (
        echo  [ERROR] Could not free port %PORT%. Kill the process manually:
        echo          netstat -ano ^| findstr :%PORT%
        echo          taskkill /F /PID ^<pid^>
        pause
        exit /b 1
    )
    echo  Port freed.
    echo.
)

echo  Starting ClipForge...
echo.
echo  Web UI:      http://localhost:%PORT%
echo  Backend log: shown live below, and mirrored in the web UI.
echo.
echo  Press Ctrl+C (then answer Y) to stop.
echo  ============================================================
echo.

rem Open the browser after a short delay so the server is up first.
start "" cmd /c "timeout /t 2 >nul & start http://localhost:%PORT%"

".venv\Scripts\python.exe" server.py %PORT%

echo.
echo  Server stopped.
pause
endlocal