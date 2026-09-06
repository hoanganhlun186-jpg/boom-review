@echo off
chcp 65001 > nul
echo ============================================
echo  BOOM Review v1.0.0 - Build ONEDIR (Nuitka)
echo ============================================
echo.
cd /d D:\hongguo\AutoRecapPro_V2\AutoRecapPro_V2

echo [1/5] Don dep output cu...
if exist dist_standalone\main.dist rmdir /s /q dist_standalone\main.dist
if exist dist_standalone\main.build rmdir /s /q dist_standalone\main.build
if exist dist_standalone\main_build_bootstrap.dist rmdir /s /q dist_standalone\main_build_bootstrap.dist
if exist dist_standalone\main_build_bootstrap.build rmdir /s /q dist_standalone\main_build_bootstrap.build

echo [2/5] Kiem tra source, vault va key...
py -3.11 -m py_compile main.py build_sanitize_config.py build_release_check.py engine\ai_engine.py
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

echo [3/5] Chuan bi certifi CA bundle...
py -3.11 -c "import certifi; print('[OK] certifi:', certifi.where())"
echo.

for /f "usebackq delims=" %%V in (`py -3.11 build_version.py prepare`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (
    echo [LOI] Khong tao duoc version build moi.
    pause
    exit /b 1
)
echo [VERSION] Build moi: %APP_VERSION%

echo [4/5] Build Nuitka ONEDIR...
py -3.11 -m nuitka ^
  --standalone ^
  --jobs=4 ^
  --windows-console-mode=disable ^
  --windows-product-name="BOOM Review" ^
  --windows-product-version=%APP_VERSION%.0 ^
  --windows-file-version=%APP_VERSION%.0 ^
  --windows-file-description="AI Video Recap Generator" ^
  --windows-company-name="Anh Studio" ^
  --windows-icon-from-ico=assets\boom_icon.ico ^
  --output-dir=dist_standalone ^
  --output-filename=BoomReview.exe ^
  --include-package=engine ^
  --include-package=core ^
  --include-package=ui ^
  --include-package=utils ^
  --include-package=capcut_tts_api ^
  --include-package=customtkinter ^
  --include-package=PIL ^
  --include-package=cv2 ^
  --include-package=numpy ^
  --include-package=edge_tts ^
  --include-package=pydub ^
  --include-package=requests ^
  --include-package=curl_cffi ^
  --include-package=charset_normalizer ^
  --include-package=certifi ^
  --include-package=urllib3 ^
  --include-package=PySide6 ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=torchaudio ^
  --nofollow-import-to=transformers ^
  --nofollow-import-to=huggingface_hub ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=librosa ^
  --nofollow-import-to=numba ^
  --nofollow-import-to=llvmlite ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=pandas ^
  --nofollow-import-to=pytest ^
  --nofollow-import-to=unittest ^
  --include-data-files=config.json=config.json ^
  --include-data-files=.build_version.txt=version.txt ^
  --include-data-dir=assets=assets ^
  --enable-plugin=pyside6 ^
  --enable-plugin=tk-inter ^
  --assume-yes-for-downloads ^
  main.py

if %ERRORLEVEL% NEQ 0 (
    py -3.11 build_version.py abort
    echo [LOI] Build that bai!
    pause
    exit /b 1
)

echo.
echo [5/5] Copy tai nguyen, lam sach config va kiem tra release...
copy /Y "C:\Windows\System32\msvcp140.dll" dist_standalone\main.dist\ >nul 2>&1
copy /Y "C:\Windows\System32\msvcp140_1.dll" dist_standalone\main.dist\ >nul 2>&1
copy /Y "C:\Windows\System32\msvcp140_2.dll" dist_standalone\main.dist\ >nul 2>&1
py -3.11 build_copy_assets.py "dist_standalone\main.dist"
if %ERRORLEVEL% NEQ 0 (
    py -3.11 build_version.py abort
    echo [LOI] Copy tai nguyen that bai.
    pause
    exit /b 1
)
py -3.11 build_sanitize_config.py "dist_standalone\main.dist\config.json"
if %ERRORLEVEL% NEQ 0 (
    py -3.11 build_version.py abort
    echo [LOI] Lam sach config release that bai.
    pause
    exit /b 1
)

py -3.11 build_release_check.py "dist_standalone\main.dist"
if %ERRORLEVEL% NEQ 0 (
    py -3.11 build_version.py abort
    echo [LOI] Release check that bai.
    pause
    exit /b 1
)

py -3.11 build_version.py commit >nul
if %ERRORLEVEL% NEQ 0 (
    echo [LOI] Build xong nhung khong ghi duoc version moi.
    pause
    exit /b 1
)

echo.
echo ============================================
echo BUILD ONEDIR HOAN TAT
echo Version: %APP_VERSION%
echo Output: dist_standalone\main.dist\BoomReview.exe
echo ============================================
pause
