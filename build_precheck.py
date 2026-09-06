import os
import re
import sys
import builtins


def main() -> int:
    sys.path.insert(0, ".")

    try:
        from engine.license_guard import SUPABASE_KEY, SUPABASE_URL

        if "YOUR_PROJECT" in SUPABASE_URL or "YOUR_SUPABASE" in SUPABASE_KEY:
            print("[WARN] Supabase chua duoc cau hinh trong engine/license_guard.py")
        else:
            print("[OK] Supabase:", SUPABASE_URL[:45])
    except Exception as exc:
        print(f"[WARN] Khong load duoc license_guard: {exc}")

    allow_unvaulted = str(os.environ.get("AUTORECAP_ALLOW_UNVAULTED_BUILD", "") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    try:
        from engine.prompt_vault import PromptVault

        vault = PromptVault()
        missing_vault = [key for key in ("recap_system", "story_writer") if not vault.is_vaulted(key)]
        if missing_vault:
            print("[ERROR] Prompt AI chua duoc ma hoa trong engine/prompt_vault.py")
            print("        Thieu token:", ", ".join(missing_vault))
            print("        Chay: py -3.11 engine/prompt_vault.py")
            print("        Copy token vao _TOKENS roi build lai.")
            if not allow_unvaulted:
                print("        Neu chi build test noi bo: set AUTORECAP_ALLOW_UNVAULTED_BUILD=1")
                return 1
        else:
            print("[OK] Prompt vault da co tokens")
    except Exception as exc:
        print(f"[WARN] Prompt vault: {exc}")

    found_key = False
    skip_dirs = {".git", ".venv", ".venv_py312", "exports", "__pycache__", "dist", "build_logs"}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(root, filename)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            if re.search(r"AIza[0-9A-Za-z\-_]{35}", text):
                print(f"[WARN] Phat hien Google API key trong: {path}")
                found_key = True
    if not found_key:
        print("[OK] Khong phat hien API key cung trong source code")

    try:
        from engine.prompt_vault import PromptVault

        vault = PromptVault()
        if vault.is_vaulted("recap_system") and vault.is_vaulted("story_writer"):
            print("[OK] Prompt AI da duoc ma hoa trong vault")
        else:
            msg = "[WARN]" if allow_unvaulted else "[ERROR]"
            print(f"{msg} Prompt chua ma hoa - build release se khong duoc tiep tuc")
            if not allow_unvaulted:
                return 1
    except Exception as exc:
        print(f"[WARN] Khong load duoc prompt_vault: {exc}")

    # The market ONEDIR build intentionally excludes Google SDK packages to
    # keep Nuitka output smaller. AIEngine must still start and use REST/Web.
    try:
        from engine.ai_engine import AIEngine

        real_import = builtins.__import__

        def _build_import(name, *args, **kwargs):
            if name in {"google.genai", "google.generativeai"}:
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _build_import
        try:
            probe = AIEngine("AIza-build-runtime-check")
        finally:
            builtins.__import__ = real_import
        if probe.api_key != "AIza-build-runtime-check":
            print("[ERROR] AIEngine khong khoi tao duoc khi market build thieu Google SDK")
            return 1
        print("[OK] AIEngine REST/Web hoat dong khong can Google SDK")
    except Exception as exc:
        try:
            builtins.__import__ = real_import
        except Exception:
            pass
        print(f"[ERROR] AIEngine con phu thuoc Google SDK luc khoi dong: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
