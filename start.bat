@echo off
rem Start Gatekeeper HR on Windows, without Docker. Double-click it, or run "start.bat".
rem (The first time, run setup.bat.)
rem
rem It opens one window per service, in this order, because each one needs the one before:
rem   Ollama (if it isn't running yet) -> backend (port 8000) -> agent (port 8001) -> page (port 5173)
rem and then opens the page in your browser. To stop a service, close its window.
rem A service that is already running is left alone.

cd /d "%~dp0"

if not exist backend\.venv (echo Run setup.bat first. & pause & exit /b 1)
if not exist agent\.venv (echo Run setup.bat first. & pause & exit /b 1)
if not exist frontend\node_modules (echo Run setup.bat first. & pause & exit /b 1)

rem ---------- Ollama ----------
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1 && (echo Ollama is already running.) || (
    echo Starting Ollama...
    start "Gatekeeper HR - Ollama" ollama serve
)

rem ---------- Backend (the CRUD API) ----------
curl -sf http://127.0.0.1:8000/health >nul 2>&1 && (echo The backend is already running.) || (
    echo Starting the backend...
    start "Gatekeeper HR - backend" /D backend cmd /k .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
)
call :wait_for http://127.0.0.1:8000/health backend || goto failed

rem ---------- Agent ----------
curl -sf http://127.0.0.1:8001/health >nul 2>&1 && (echo The agent is already running.) || (
    echo Starting the agent...
    start "Gatekeeper HR - agent" /D agent cmd /k .venv\Scripts\python -m uvicorn app.main:app --reload --port 8001
)
call :wait_for http://127.0.0.1:8001/health agent || goto failed

rem ---------- The web page ----------
curl -s http://localhost:5173 >nul 2>&1 && (echo The page is already running.) || (
    echo Starting the page...
    start "Gatekeeper HR - page" /D frontend cmd /k npm run dev
)
call :wait_for http://localhost:5173 page || goto failed

echo.
echo Gatekeeper HR is running: http://localhost:5173
echo Log in as sara.wilson@gatekeeper.local (or leila.ali / grace.raza), password Password123!
start "" http://localhost:5173
exit /b 0

:failed
echo.
echo Something did not start: look at its window for the error.
pause
exit /b 1

rem ---------- wait_for <address> <name>: wait up to 60 seconds until the address answers ----------
:wait_for
for /l %%i in (1,1,60) do (
    curl -sf %1 >nul 2>&1 && exit /b 0
    rem "ping" twice = wait about one second (works everywhere, unlike "timeout" in some windows)
    ping -n 2 127.0.0.1 >nul
)
echo The %2 did not answer at %1 after 60 seconds.
exit /b 1
