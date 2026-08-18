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
