@echo off
echo Setting up DataFlow...

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate and install
call venv\Scripts\activate.bat
pip install -r requirements.txt

REM Run the app
echo.
echo Starting DataFlow at http://localhost:5000
echo Default login: admin / admin123
echo Press Ctrl+C to stop.
echo.
python app.py
