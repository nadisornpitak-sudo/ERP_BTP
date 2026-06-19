@echo off
echo ==========================================
echo   BTP ERP Server — Butterfly Thai Perfume
echo ==========================================
cd /d %~dp0
echo Installing/updating dependencies...
pip install -r requirements.txt -q
echo.
echo Server running at: http://localhost:8000
echo Default login: admin / admin1234
echo.
echo Press Ctrl+C to stop the server.
echo.
uvicorn main:app --reload --port 8000
pause
