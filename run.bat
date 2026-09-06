@echo off
setlocal

cd /d "%~dp0"
echo Starting AutoRecapPro_V2...

set "FFMPEG_HOME=D:\ffmpeg-8.0.1-essentials_build\bin"
set "PATH=%FFMPEG_HOME%;%PATH%"

echo Checking dependencies...
py -3.11 -m pip show customtkinter >nul 2>&1
if errorlevel 1 (
    echo Installing requirements...
    py -3.11 -m pip install --user -r requirements.txt
    if errorlevel 1 goto :fail
)

py -3.11 main.py
if errorlevel 1 goto :fail

goto :eof

:fail
echo.
echo Failed to start app.
pause

endlocal
