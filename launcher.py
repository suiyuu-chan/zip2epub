"""PyInstaller 打包入口（单文件、无控制台）。

- 双击（无参数）：启动图形界面
- 从终端带参数：走命令行；输出写回调用它的终端（或重定向文件）
"""

from __future__ import annotations

import os
import sys


def _stdout_available() -> bool:
    """窗口版 exe 启动时 sys.stdout 为 None，先看底层句柄是否仍然有效（重定向场景）。"""
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        return handle not in (0, None) and handle != ctypes.c_void_p(-1).value
    except Exception:
        return False


def _ensure_console() -> bool:
    if sys.stdout is not None and sys.stderr is not None:
        return True

    # 1) 有有效的 stdout 句柄（管道 / 重定向到文件）：直接恢复
    if _stdout_available():
        try:
            sys.stdout = os.fdopen(1, "w", encoding="utf-8", errors="replace", closefd=False, buffering=1)
            sys.stderr = os.fdopen(2, "w", encoding="utf-8", errors="replace", closefd=False, buffering=1)
            return True
        except OSError:
            pass

    # 2) 从终端启动：附着到父控制台
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            return True
    except Exception:
        pass
    return False


def main() -> int:
    args = sys.argv[1:]

    if not args:
        from gui.app import main as gui_main

        return gui_main()

    from z2e.cli import main as cli_main

    if not _ensure_console():
        # 双击却带了参数：给个提示，避免“什么都没发生”
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "zip2epub",
                "命令行模式需要在终端里运行，例如：\n\n"
                "  zip2epub.exe convert D:\\comics -o D:\\books --jobs 4\n"
                "  zip2epub.exe --help\n\n"
                "双击 zip2epub.exe 会直接打开图形界面。",
            )
            root.destroy()
        except Exception:
            pass
        return 2

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
