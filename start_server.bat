@echo off
rem Start the Sandeep AI Command Center web UI on http://127.0.0.1:8000
rem Usage: double-click, or run:  start_server.bat
setlocal
set PYTHON=C:\Users\Ats\AppData\Local\Python\bin\python.exe
set DIR=C:\Users\Ats\OneDrive\Documents\Sandeep-AI-Command-Center-role-setup
cd /d "%DIR%"
start "AI Command Center" /min "%PYTHON%" -m uvicorn webapi.app:app --host 127.0.0.1 --port 8000
echo Starting server at http://127.0.0.1:8000 ... (wait a few seconds, then open in browser)
