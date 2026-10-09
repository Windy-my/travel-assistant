@echo off
REM =====================================================================
REM  Travel Assistant - Backend launcher (double-click to start FastAPI)
REM  Uses the project-local virtual environment .venv (Python 3.12.7)
REM  Backend API will listen on http://localhost:8000  (docs: /docs)
REM =====================================================================
setlocal

REM cd to this script's own directory so it works no matter where it is launched from
cd /d "%~dp0"

set "VENV=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV%" (
    echo [ERROR] Virtual environment not found: %VENV%
    echo         Run  python -m venv .venv  then  .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting Travel Assistant backend from: %~dp0
echo Opening http://localhost:8000/docs in your browser...
start "" http://localhost:8000/docs
echo.

"%VENV%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

echo.
echo Backend stopped.
endlocal
