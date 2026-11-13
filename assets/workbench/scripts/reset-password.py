"""A local, user-operated password reset window. Never accepts passwords via CLI."""
from pathlib import Path
import sqlite3
import sys
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import data_directory
from app.security import password_hash


def main():
    database = data_directory() / "workbench.sqlite3"
    root = tk.Tk()
    root.title("AI创作台 · 重新设置密码")
    root.geometry("470x355")
    root.resizable(False, False)
    panel = ttk.Frame(root, padding=24)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="重新设置工作台密码", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
    ttk.Label(panel, text="电脑和手机使用同一个密码。请设置至少8位的新密码。", wraplength=420).pack(anchor="w", pady=(12, 14))
    first, second = tk.StringVar(), tk.StringVar()
    ttk.Label(panel, text="新密码").pack(anchor="w")
    entry = ttk.Entry(panel, textvariable=first, show="•", width=48)
    entry.pack(fill="x", pady=(4, 10))
    ttk.Label(panel, text="再输入一次").pack(anchor="w")
    confirm = ttk.Entry(panel, textvariable=second, show="•", width=48)
    confirm.pack(fill="x", pady=(4, 10))
    error = tk.StringVar()
    ttk.Label(panel, textvariable=error, foreground="#b43f3f").pack(anchor="w")
    show = tk.BooleanVar()
    ttk.Checkbutton(panel, text="显示密码", variable=show,
                    command=lambda: [field.configure(show="" if show.get() else "•") for field in (entry, confirm)]).pack(anchor="w", pady=(4, 10))

    def save():
        password = first.get()
        if not 8 <= len(password) <= 256:
            error.set("请使用8至256位密码。")
            return
        if password != second.get():
            error.set("两次输入不同，请重新核对。")
            return
        if not database.is_file():
            error.set("没有找到本工作台的数据，请先运行启动工作台。")
            return
        try:
            hashed = password_hash(password)
            with sqlite3.connect(database, timeout=30) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if not connection.execute("SELECT id FROM auth WHERE id=1").fetchone():
                    raise RuntimeError("请先在工作台首次设置页创建密码。")
                connection.execute("UPDATE auth SET password_hash=? WHERE id=1", (hashed,))
                connection.execute("DELETE FROM sessions")
            first.set("")
            second.set("")
            messagebox.showinfo("密码已更新", "密码已更新。手机用新密码登录即可。\n电脑页面刷新后，也需要重新登录。", parent=root)
            print("Password reset completed; existing sessions revoked.", flush=True)
            root.destroy()
        except (OSError, sqlite3.Error):
            error.set("暂时无法更新密码，请稍后重试。")
        except RuntimeError as exc:
            error.set(str(exc))

    ttk.Button(panel, text="保存新密码", command=save).pack(anchor="e")
    root.bind("<Return>", lambda _: save())
    entry.focus_set()
    root.lift()
    root.attributes("-topmost", True)
    root.after(1500, lambda: root.attributes("-topmost", False))
    root.mainloop()


if __name__ == "__main__":
    main()
