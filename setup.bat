@echo off
rem First-time setup of Gatekeeper HR on Windows, without Docker. Double-click it, or run "setup.bat".
rem Run it again after you pull new code: every step is safe to repeat.
rem
rem You need: Python 3.10+, Node 20+, PostgreSQL (with an empty database called gatekeeper_hr)
rem and Ollama, all installed. Then:
rem   1. installs the backend, migrates the database, creates the API's database user, adds demo data
rem   2. installs the agent service
rem   3. installs the web page
rem   4. downloads the AI model
rem Then start everything with start.bat.

rem Run from this file's folder, wherever it was started from.
cd /d "%~dp0"

rem ---------- Check the programs we need ----------
where python >nul 2>&1 || (echo Python is not installed, or not on the PATH. & goto failed)
where npm >nul 2>&1 || (echo Node.js is not installed, or not on the PATH. & goto failed)
where ollama >nul 2>&1 || (echo Ollama is not installed, or not on the PATH. & goto failed)

rem ---------- The settings files ----------
rem backend\.env holds passwords, so you fill it in yourself (only once).
if not exist backend\.env (
    copy backend\.env.example backend\.env >nul
    echo Created backend\.env. Open it, fill in the passwords ^(see backend\README.md^),
    echo then run setup.bat again.
    goto failed
)
rem agent\.env has no secrets: the example works as it is.
if not exist agent\.env copy agent\.env.example agent\.env >nul

rem ---------- 1. Backend ----------
echo.
echo === 1/4 Backend ===
cd backend
if not exist .venv (python -m venv .venv || goto failed)
.venv\Scripts\python -m pip install --quiet -r requirements.txt || goto failed
rem The tables (as the owner), then the API's own limited database user (see create_app_user.py).
.venv\Scripts\alembic upgrade head || goto failed
.venv\Scripts\python create_app_user.py || goto failed
rem The demo company. It only fills EMPTY tables, so running it again changes nothing.
.venv\Scripts\python seed.py || goto failed
cd ..

rem ---------- 2. Agent ----------
echo.
echo === 2/4 Agent ===
cd agent
if not exist .venv (python -m venv .venv || goto failed)
.venv\Scripts\python -m pip install --quiet -r requirements.txt || goto failed
cd ..

rem ---------- 3. Web page ----------
echo.
echo === 3/4 Web page ===
cd frontend
rem "call": npm is itself a batch file, and without "call" this file would stop after it.
call npm install --no-fund --no-audit || goto failed
cd ..

rem ---------- 4. The AI model ----------
echo.
echo === 4/4 AI model (about 2.5 GB the first time) ===
ollama pull qwen3:4b-instruct || goto failed

echo.
echo Setup done. Now run start.bat.
pause
exit /b 0

:failed
echo.
echo Setup stopped. Fix the problem above, then run setup.bat again.
pause
exit /b 1
