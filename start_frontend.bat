@echo off
REM =====================================================================
REM  Travel Assistant - Frontend launcher (Streamlit web UI)
REM  Double-click AFTER the backend is running (start_backend.bat).
REM  If the backend is not running on port 8000, this script will tell you.
REM =====================================================================
setlocal

cd /d "%~dp0"

set "VENV=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV%" (
    echo [ERROR] Virtual environment not found: %VENV%
    echo         Run  python -m venv .venv  then  .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM check whether the backend is already listening on 127.0.0.1:8000
netstat -ano | findstr /R ":8000 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Backend not detected on http://127.0.0.1:8000
    echo        Please double-click  start_backend.bat  first, then run this again.
    echo        (Demo/OpenStreetMap data still needs the backend.)
    echo.
)

REM point the UI at the backend and start Streamlit
set "BACKEND_URL=http://127.0.0.1:8000"
echo Starting Travel Assistant web UI ...
echo When ready, your browser opens at http://localhost:8501
start "" http://localhost:8501

"%VENV%" -m streamlit run frontend\app.py --server.port 8501 --server.address 127.0.0.1

echo.
echo Frontend stopped.
endlocal
