@echo off
cd /d "%~dp0"
echo ========================================
echo  BOOM Review - Build voi Nuitka
echo ========================================

python -m nuitka ^
  --standalone ^
  --jobs=2 ^
  --windows-console-mode=disable ^
  --windows-product-name="BOOM Review" ^
  --windows-product-version=1.0.0.0 ^
  --windows-file-version=1.0.0.0 ^
  --windows-file-description="AI Video Recap Generator" ^
  --windows-company-name="Anh Studio" ^
  --windows-icon-from-ico=assets\boom_icon.ico ^
  --output-dir=dist ^
  --output-filename=BoomReview.exe ^
  --include-package=engine ^
  --include-package=core ^
  --include-package=ui ^
  --include-package=utils ^
  --include-package=capcut_tts_api ^
  --include-package=curl_cffi ^
  --include-package=PySide6 ^
  --include-package=google ^
  --include-package=requests ^
  --include-package-data=customtkinter ^
  --include-package-data=edge_tts ^
  --include-package-data=certifi ^
  --nofollow-import-to=librosa ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=numba ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=pytest ^
  --nofollow-import-to=unittest ^
  --nofollow-import-to=pyautogui ^
  --enable-plugin=pyside6 ^
  --enable-plugin=tk-inter ^
  --assume-yes-for-downloads ^
  main.py

if errorlevel 1 (
  echo.
  echo [LOI] Nuitka build that bai!
  pause
  exit /b 1
)

echo.
echo [OK] Nuitka build xong!

echo.
echo Sao chep assets, certifi, version...
python -c "
import shutil, certifi
from pathlib import Path
dist = Path('dist/main.dist')
shutil.copytree('assets', dist / 'assets', dirs_exist_ok=True)
shutil.copy2(certifi.where(), dist / 'cacert.pem')
(dist / 'capcut_device.json').unlink(missing_ok=True)
shutil.copy2('version.txt', dist / 'version.txt')
print('Assets copied OK')
"

echo.
echo Sao chep ffmpeg...
if exist ffmpeg.exe copy /Y ffmpeg.exe dist\main.dist\ffmpeg.exe
if exist ffprobe.exe copy /Y ffprobe.exe dist\main.dist\ffprobe.exe
if exist ffplay.exe copy /Y ffplay.exe dist\main.dist\ffplay.exe

echo.
echo Sao chep models...
if exist models (
  xcopy /E /I /Y models dist\main.dist\models >nul
  echo Models copied OK
) else (
  echo [CANH BAO] Khong tim thay thu muc models\
)

echo.
echo Sao chep data\tools...
if exist data\tools (
  xcopy /E /I /Y data\tools dist\main.dist\data\tools >nul
  echo data\tools copied OK
) else (
  echo [CANH BAO] Khong tim thay thu muc data\tools\
)

echo.
echo Sanitize config...
if exist build_sanitize_config.py (
  python build_sanitize_config.py "dist\main.dist\config.json"
)

echo.
echo ========================================
echo  Build hoan tat!
echo  Thu muc output: dist\main.dist\
echo  File exe: dist\main.dist\BoomReview.exe
echo ========================================
pause
