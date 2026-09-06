@echo off
chcp 65001 > nul
echo ============================================
echo  AutoRecapPro V2 - Build DEBUG (co console)
echo ============================================
echo.
cd /d D:\AutoRecapPro_V2

echo [1/5] Don dep output debug cu...
if exist dist_debug\main.dist rmdir /s /q dist_debug\main.dist
if exist dist_debug\main.build rmdir /s /q dist_debug\main.build
if exist dist_debug\main_build_bootstrap.dist rmdir /s /q dist_debug\main_build_bootstrap.dist
if exist dist_debug\main_build_bootstrap.build rmdir /s /q dist_debug\main_build_bootstrap.build

echo [2/5] Kiem tra source, vault va release-check...
py -3.11 -m py_compile main.py main_build_bootstrap.py build_sanitize_config.py build_release_check.py engine\ai_engine.py engine\capcut_integration.py engine\prompt_vault.py engine\license_guard.py
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Source co loi syntax.
    pause
    exit /b 1
)
py -3.11 build_precheck.py
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Build precheck that bai.
    pause
    exit /b 1
)

echo [3/5] Patch certifi...
py -3.11 fix_certifi_source.py
echo.

echo [4/5] Build Nuitka DEBUG (voi console de thay loi)...
REM Gemini text/Vision dung REST; SDK Google nang khong can dong vao debug build.
py -3.11 -m nuitka ^
  --standalone ^
  --jobs=4 ^
  --windows-console-mode=force ^
  --output-dir=dist_debug ^
  --output-filename=AutoRecapPro_V2_debug.exe ^
  --windows-icon-from-ico=assets\app_icon.ico ^
  --include-module=main ^
  --include-package=customtkinter ^
  --include-package=PIL ^
  --include-package=cv2 ^
  --include-package=numpy ^
  --include-package=edge_tts ^
  --include-package=piper ^
  --include-package=selenium ^
  --include-package=webdriver_manager ^
  --include-package=pydub ^
  --include-package=requests ^
  --include-package=curl_cffi ^
  --include-package=charset_normalizer ^
  --include-package=certifi ^
  --nofollow-import-to=google.genai ^
  --nofollow-import-to=google.generativeai ^
  --nofollow-import-to=google.api_core ^
  --nofollow-import-to=google.auth ^
  --nofollow-import-to=google.oauth2 ^
  --nofollow-import-to=google.protobuf ^
  --nofollow-import-to=google.rpc ^
  --nofollow-import-to=grpc ^
  --nofollow-import-to=grpc_status ^
  --nofollow-import-to=proto ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=torchaudio ^
  --nofollow-import-to=transformers ^
  --nofollow-import-to=lightning ^
  --nofollow-import-to=huggingface_hub ^
  --nofollow-import-to=tokenizers ^
  --nofollow-import-to=safetensors ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=librosa ^
  --nofollow-import-to=numba ^
  --nofollow-import-to=llvmlite ^
  --nofollow-import-to=av ^
  --nofollow-import-to=PySide6 ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=pandas ^
  --nofollow-import-to=pytest ^
  --nofollow-import-to=unittest ^
  --include-data-files=config.json=config.json ^
  --include-data-files=version.txt=version.txt ^
  --include-data-dir=assets=assets ^
  --plugin-enable=tk-inter ^
  --assume-yes-for-downloads ^
  main_build_bootstrap.py

if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Build that bai!
    pause
    exit /b 1
)

echo.
echo [5/5] Copy models/tools va kiem tra debug release...
copy /Y "C:\Windows\System32\msvcp140.dll" dist_debug\main_build_bootstrap.dist\ >nul 2>&1
copy /Y "C:\Windows\System32\msvcp140_1.dll" dist_debug\main_build_bootstrap.dist\ >nul 2>&1
copy /Y "C:\Windows\System32\msvcp140_2.dll" dist_debug\main_build_bootstrap.dist\ >nul 2>&1
py -3.11 build_copy_assets.py "dist_debug\main_build_bootstrap.dist"
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Copy tai nguyen that bai.
    pause
    exit /b 1
)
py -3.11 build_sanitize_config.py "dist_debug\main_build_bootstrap.dist\config.json"
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Lam sach config debug that bai.
    pause
    exit /b 1
)
py -3.11 build_release_check.py "dist_debug\main_build_bootstrap.dist"
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Release check that bai.
    pause
    exit /b 1
)

echo.
echo ============================================
echo BUILD DEBUG XONG
echo Chay: dist_debug\main_build_bootstrap.dist\AutoRecapPro_V2_debug.exe
echo Se hien console voi loi ro rang
echo ============================================
pause
