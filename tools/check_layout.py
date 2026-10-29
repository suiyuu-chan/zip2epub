"""布局检查：在小窗口下确认所有按钮都在可视区域内。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from gui.app import Zip2EpubApp  # noqa: E402


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def check(app: Zip2EpubApp, width: int, height: int) -> bool:
    app.geometry(f"{width}x{height}")
    app.update()
    app.update_idletasks()

    win_w = app.winfo_width()
    win_h = app.winfo_height()
    ok = True
    print(f"--- window {win_w}x{win_h} ---")
    for widget in walk(app):
        if not isinstance(widget, (ttk.Button, tk.Button)):
            continue
        if not widget.winfo_ismapped():
            continue
        x = widget.winfo_rootx() - app.winfo_rootx()
        y = widget.winfo_rooty() - app.winfo_rooty()
        right = x + widget.winfo_width()
        bottom = y + widget.winfo_height()
        text = widget.cget("text")
        inside = right <= win_w and bottom <= win_h and x >= 0 and y >= 0
        ok = ok and inside
        flag = "OK " if inside else "CLIPPED"
        print(f"  {flag} {text!r:14s} x={x:4d} right={right:4d} bottom={bottom:4d}")
    return ok


def main() -> int:
    app = Zip2EpubApp()
    app.update()
    results = [check(app, 900, 600), check(app, 1080, 720), check(app, 1280, 860)]
    app.destroy()
    print("ALL VISIBLE" if all(results) else "SOME CLIPPED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
