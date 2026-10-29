@echo off
title Genshin Auto Edit Bot (gacard)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m gacard.bot
pause
