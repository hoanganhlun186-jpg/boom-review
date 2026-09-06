import subprocess
import sys
import shutil
from pathlib import Path

from build_version import STAGED_FILE, abort, commit, prepare


APP_DIR = Path(r"D:\hongguo\AutoRecapPro_V2\AutoRecapPro_V2")
DIST_DIR = APP_DIR / "dist_standalone"
LOG_DIR = APP_DIR / "build_logs"
LOG_FILE = LOG_DIR / "nuitka_build.log"


def main() -> int:
    if any(item.lower() in {"-h", "--help", "help"} for item in sys.argv[1:]):
        print("Usage:")
        print("  py -3.11 build_nuitka.py onefile")
        print("  py -3.11 build_nuitka.py onedir")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    build_mode = "onefile"
    for item in sys.argv[1:]:
        if item.lower() in {"onefile", "--onefile"}:
            build_mode = "onefile"
        elif item.lower() in {"onedir", "--onedir", "standalone", "--standalone"}:
            build_mode = "onedir"

    mode_args = ["--onefile"] if build_mode == "onefile" else ["--standalone"]
    version = prepare()
    windows_version = f"{version}.0"
    print("[VERSION] Build moi:", version)
    args = [
        sys.executable,
        "-m",
        "nuitka",
        *mode_args,
        "--windows-console-mode=disable",
        "--product-name=BOOM Review",
        f"--product-version={windows_version}",
        f"--file-version={windows_version}",
        "--file-description=AI Video Recap Generator",
        "--company-name=Anh Studio",
        f"--windows-icon-from-ico={APP_DIR / 'assets' / 'boom_icon.ico'}",
        f"--output-dir={DIST_DIR}",
        "--output-filename=BoomReview.exe",
        "--nofollow-imports",
        "--follow-import-to=engine",
        "--follow-import-to=core",
        "--follow-import-to=ui",
        "--follow-import-to=utils",
        "--follow-import-to=capcut_tts_api",
        "--follow-import-to=config",
        "--include-package=engine",
        "--include-package=core",
        "--include-package=ui",
        "--include-package=utils",
        "--include-package=capcut_tts_api",
        "--include-package=curl_cffi",
        "--include-package=PySide6",
        "--include-package-data=customtkinter",
        "--nofollow-import-to=librosa",
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=numba",
        "--nofollow-import-to=torch",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=jupyter",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=doctest",
        "--nofollow-import-to=*.tests",
        "--nofollow-import-to=*.testing",
        "--enable-plugin=pyside6",
        "--enable-plugin=tk-inter",
        "--show-progress",
        "--jobs=4",
        "--noinclude-data-files=exports/*",
        "--noinclude-data-files=.git/*",
        "--noinclude-data-files=docs/*",
        "--noinclude-data-files=server/*",
        "--noinclude-data-files=test_*.py",
        "--noinclude-data-files=_test_*.py",
        "--noinclude-data-files=*.md",
        "--noinclude-data-files=*.bak",
        "--noinclude-data-files=main.py.bak",
        "--noinclude-data-files=nul",
        f"--include-data-files={STAGED_FILE}=version.txt",
        "--assume-yes-for-downloads",
        str(APP_DIR / "main.py"),
    ]

    print("[MODE] Nuitka build:", build_mode)
    print("[LOG] Nuitka log:", LOG_FILE)
    with LOG_FILE.open("w", encoding="utf-8", errors="replace") as log:
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            abort()
            raise
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        return_code = proc.wait()
        if return_code == 0:
            shutil.copy2(STAGED_FILE, DIST_DIR / "version.txt")
            commit()
            print("[VERSION] Da phat hanh:", version)
        else:
            abort()
            print("[VERSION] Build loi, giu nguyen version cu")
        return return_code


if __name__ == "__main__":
    raise SystemExit(main())
