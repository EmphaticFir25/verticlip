@echo off
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || goto :fail

if not exist .env (
    copy .env.example .env >nul
    echo Created .env from .env.example. Fill in your keys, then run this file again.
    pause
    exit /b 0
)

echo Starting Verticlip at http://localhost:33214
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:33214"
uvicorn server:app --host 127.0.0.1 --port 33214
goto :eof

:fail
echo Setup failed. Check that Python 3.10+ is installed and on PATH.
pause
exit /b 1
