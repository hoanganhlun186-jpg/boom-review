@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   AutoRecapPro V2 - Build EXE (Nuitka)
echo ============================================================
echo.

set "APP_DIR=D:\AutoRecapPro_V2"
set "DIST_DIR=%APP_DIR%\dist"
set "LOG_DIR=%APP_DIR%\build_logs"
set "BUILD_LOG=%LOG_DIR%\nuitka_build.log"
set "PYTHON=py -3.11"

cd /d "%APP_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ── Kiểm tra Nuitka ──────────────────────────────────────────
%PYTHON% -c "import nuitka, ordered_set, zstandard; print('ok')" >nul 2>&1
if errorlevel 1 (
    REM Thu lai voi pip show
    %PYTHON% -m pip show nuitka >nul 2>&1
    if errorlevel 1 (
        echo [LỖI] Nuitka chưa được cài. Chạy lệnh sau:
        echo   py -3.11 -m pip install nuitka==2.4.8 zstandard ordered-set
        pause
        exit /b 1
    )
    echo [OK] Nuitka da cai qua pip
) else (
    echo [OK] Nuitka san sang
)

REM ── Xóa build cũ ─────────────────────────────────────────────
echo [1/5] Dọn dẹp build cũ...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "main.build"  rmdir /s /q "main.build"
if exist "main.dist"   rmdir /s /q "main.dist"
if exist "main.onefile-build" rmdir /s /q "main.onefile-build"
mkdir "%DIST_DIR%"

REM ── Kiểm tra config license đã điền chưa ─────────────────────
echo [2/5] Kiểm tra cấu hình và mã hóa prompts...
%PYTHON% build_precheck.py
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Build precheck failed.
    pause
    exit /b 1
)

REM ── Chạy Nuitka build ────────────────────────────────────────
echo [3/5] Build với Nuitka (có thể mất 10-30 phút)...
echo [LOG] Nuitka log: %BUILD_LOG%
echo.

%PYTHON% build_nuitka.py

if errorlevel 1 (
    echo.
    echo [LỖI] Build thất bại! Xem lỗi phía trên.
    echo [LOG] File lỗi: %BUILD_LOG%
    powershell -NoProfile -Command "Get-Content -LiteralPath '%BUILD_LOG%' -Tail 120"
    pause
    exit /b 1
)

REM ── Kiểm tra output ──────────────────────────────────────────
echo.
echo [4/5] Kiểm tra output...
set "EXE=%DIST_DIR%\AutoRecapPro_V2.exe"
if not exist "%EXE%" (
    echo [LỖI] Không tìm thấy EXE tại: %EXE%
    pause
    exit /b 1
)

%PYTHON% build_release_check.py "%DIST_DIR%"
if errorlevel 1 (
    echo [ERROR] Output co nguy co lo source. Build bi dung lai.
    pause
    exit /b 1
)

for %%A in ("%EXE%") do (
    set "SIZE=%%~zA"
    set /a SIZE_MB=!SIZE! / 1048576
    echo [OK] EXE: %EXE%
    echo [OK] Kích thước: !SIZE_MB! MB
)

REM ── Tạo README cho user ──────────────────────────────────────
echo [5/5] Tạo README_USER.txt...
(
echo AutoRecapPro V2 - Hướng dẫn sử dụng
echo =====================================
echo.
echo YÊU CẦU:
echo - Windows 10/11 64-bit
echo - Google Chrome đã cài sẵn ^(https://www.google.com/chrome/^)
echo - Kết nối internet ^(để xác thực bản quyền và dùng Gemini AI^)
echo.
echo KÍCH HOẠT:
echo - Chạy AutoRecapPro_V2.exe
echo - Màn hình sẽ hiện "Mã máy" của bạn
echo - Gửi mã máy cho admin để được kích hoạt
echo - Sau khi kích hoạt, restart app
echo.
echo SỬ DỤNG:
echo - Nhập Gemini API key của bạn vào ô "Gemini API Key"
echo   ^(Lấy miễn phí tại: https://aistudio.google.com/apikey^)
echo - Chọn video gốc và chạy Full Pipeline
echo.
echo HỖ TRỢ: Liên hệ admin qua Zalo/Telegram
) > "%DIST_DIR%\README_USER.txt"

echo.
echo ============================================================
echo   BUILD HOÀN TẤT!
echo   Output: %DIST_DIR%\AutoRecapPro_V2.exe
echo ============================================================
echo.
echo Bước tiếp theo:
echo   1. Test EXE trên máy không có Python
echo   2. Thêm HWID máy test vào Supabase
echo   3. Đóng gói dist\ vào ZIP để phân phối
echo.
pause
