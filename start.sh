#!/usr/bin/env bash
# Start CMRSARathi Criterion 5 on Mac/Linux: ./start.sh   then open http://127.0.0.1:5000
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
pip install -q -r requirements.txt
flask --app run seed-demo
echo "Open http://127.0.0.1:5000  (login: principal / demo-password; also hod, head, member)"
python run.py
