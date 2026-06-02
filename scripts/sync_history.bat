@echo off
setlocal
set "ROOT=C:\Users\c\projects\autotrader"
set "LOGFILE=%ROOT%\data\sync.log"
set "PYTHONUNBUFFERED=1"
if "%~1"=="" (set "KIS_ENV=mock") else (set "KIS_ENV=%~1")
cd /d "%ROOT%"
echo. >> "%LOGFILE%"
echo [%date% %time%] env=%KIS_ENV% >> "%LOGFILE%"
"%ROOT%\.venv\Scripts\python.exe" -u -m trader.history >> "%LOGFILE%" 2>&1
endlocal
