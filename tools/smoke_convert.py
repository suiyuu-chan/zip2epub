"""GUI 转换冒烟：模拟选择输出目录并跑完整批处理（自动关闭弹窗）。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter.messagebox as mb  # noqa: E402

mb.askyesno = lambda *a, **k: False
mb.showerror = lambda *a, **k: print("ERRORBOX:", a[:1])
mb.showinfo = lambda *a, **k: print("INFOBOX:", a[:1])
mb.showwarning = lambda *a, **k: print("WARNBOX:", a[:1])

from gui.app import Zip2EpubApp  # noqa: E402


def main() -> int:
    app = Zip2EpubApp()
    app.update()

    app._add_paths([ROOT / "sample_data"])
    app.update()
    print("books:", len(app.books))

    output = ROOT / "out" / "gui-check"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    app._output_auto.set(False)
    app.output_entry.configure(state="normal")
    app.output_entry.delete(0, "end")
    app.output_entry.insert(0, str(output))
    app.output_entry.configure(state="disabled")
    print("chosen output:", app._output_dir())

    app.start_convert()
    deadline = time.time() + 90
    while app._busy and time.time() < deadline:
        app.update()
        time.sleep(0.05)
    app.update()

    print("status:", app.status_var.get())
    files = sorted(output.glob("*.epub"))
    print("outputs:", len(files))
    for path in files:
        print(f"  {path.name}  {path.stat().st_size:,} B")

    app.destroy()
    return 0 if len(files) == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
