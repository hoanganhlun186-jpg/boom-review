import os
import re
import subprocess
import tkinter as tk
from tkinter import messagebox

VERSION_FILE = "version.txt"
MAIN_FILE = "main.py"


def get_current_version():
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if re.fullmatch(r"\d+\.\d+\.\d+", v):
            return v
    except Exception:
        pass
    return "1.0.0"


def bump_version(current_version):
    parts = [int(x) for x in current_version.split(".")]
    parts[2] += 1
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def update_version_file(new_version):
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(new_version)


def restore_version_file(old_version):
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(old_version)
    except Exception:
        pass


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out.strip()


def _current_branch():
    ok, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out if ok and out else "main"


def publish():
    current = get_current_version()
    new_ver = bump_version(current)
    tag_name = f"v{new_ver}"
    branch = _current_branch()
    tag_created = False

    try:
        update_version_file(new_ver)

        ok, out = _run(["git", "add", "."])
        if not ok:
            raise RuntimeError(f"git add lỗi:\n{out}")

        ok_diff, _ = _run(["git", "diff", "--cached", "--quiet"])
        if ok_diff:
            raise RuntimeError("Không có thay đổi nào để phát hành.")

        ok, out = _run(["git", "commit", "-m", f"Release {tag_name}"])
        if not ok:
            raise RuntimeError(f"git commit lỗi:\n{out}")

        ok, out = _run(["git", "tag", tag_name])
        if not ok:
            raise RuntimeError(f"git tag lỗi (tag {tag_name} có thể đã tồn tại):\n{out}")
        tag_created = True

        ok, out = _run(["git", "push", "origin", branch])
        if not ok:
            raise RuntimeError(f"git push nhánh '{branch}' lỗi:\n{out}")

        ok, out = _run(["git", "push", "origin", tag_name])
        if not ok:
            raise RuntimeError(f"git push tag lỗi:\n{out}")

        lbl_ver.config(text=f"Phiên bản hiện tại: v{new_ver}", fg="green")
        messagebox.showinfo(
            "Thành công",
            f"Đã đẩy phiên bản {tag_name} lên GitHub (nhánh '{branch}')!\n\n"
            "GitHub Actions đang tự động build BoomReview.exe.\n"
            "Khoảng 20-40 phút sẽ có file exe trong tab Releases.")
        root.destroy()

    except Exception as e:
        if tag_created:
            _run(["git", "tag", "-d", tag_name])
        restore_version_file(current)
        messagebox.showerror(
            "Lỗi",
            f"Có lỗi xảy ra:\n{str(e)}\n\n"
            "Đã hoàn tác (version cũ được giữ nguyên).\n"
            "Kiểm tra: đã 'git init' và 'git remote add origin' chưa?")


root = tk.Tk()
root.title("Publish - BOOM Review")
root.geometry("380x160")
root.eval('tk::PlaceWindow . center')
root.resizable(False, False)

tk.Label(root, text="🚀 Phát Hành Bản Cập Nhật BOOM Review",
         font=("Arial", 12, "bold")).pack(pady=10)
lbl_ver = tk.Label(root, text=f"Phiên bản hiện tại: v{get_current_version()}", fg="blue")
lbl_ver.pack()
tk.Label(root, text="(Bấm nút để tăng số version và push lên GitHub)", fg="gray",
         font=("Arial", 9)).pack()

tk.Button(root, text="⬆ Nâng cấp & Phát hành ngay", bg="#10b981", fg="white",
          font=("Arial", 10, "bold"), command=publish).pack(pady=15)

root.mainloop()
