@echo off
title Edit Genshin Local Web UI (Port 8001)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m gacard.webapp --port 8001
pause
