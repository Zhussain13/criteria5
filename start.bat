@echo off
REM Start CMRSARathi Criterion 5 on Windows: double-click start.bat, then open http://127.0.0.1:5000
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv || python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
flask --app run seed-demo
echo Open http://127.0.0.1:5000  (login: principal / demo-password; also hod, head, member)
start "" http://127.0.0.1:5000
python run.py
pause
