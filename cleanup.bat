@echo off
echo ================================
echo   BOOM Review - Don dep project
echo ================================
echo.

set ROOT=D:\hongguo\AutoRecapPro_V2\AutoRecapPro_V2

echo Xoa cache va build output...
rd /s /q "%ROOT%\__pycache__" 2>nul
rd /s /q "%ROOT%\.pytest_cache" 2>nul
rd /s /q "%ROOT%\dist" 2>nul
rd /s /q "%ROOT%\server" 2>nul
rd /s /q "%ROOT%\exports" 2>nul
rd /s /q "%ROOT%\Claude outputs" 2>nul

echo Xoa file tam va rac...
del /f /q "%ROOT%\main.py.bak" 2>nul
del /f /q "%ROOT%\nuitka-crash-report.xml" 2>nul
del /f /q "%ROOT%\temp_v2.mp3" 2>nul
del /f /q "%ROOT%\temp_v2_advanced.mp3" 2>nul
del /f /q "%ROOT%\test_en-US-JennyNeural.mp3" 2>nul
del /f /q "%ROOT%\test_tts.mp3" 2>nul
del /f /q "%ROOT%\test_vi-VN-HoaiMyNeural.mp3" 2>nul
del /f /q "%ROOT%\test_vi-VN-NamMinhNeural.mp3" 2>nul
del /f /q "%ROOT%\py_run_log.txt" 2>nul
del /f /q "%ROOT%\_nul" 2>nul

echo.
echo Xong! Da don dep project.
pause
