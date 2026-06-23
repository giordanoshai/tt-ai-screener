@echo off
REM Daily data update for TT AI Screener
REM Schedule with: schtasks /create /tn "tt-trading-update" /tr "%~dp0run_daily_update.bat" /sc daily /st 05:00

cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m db.update
