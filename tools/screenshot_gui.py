"""无交互截图：用于检查 GUI 布局（会短暂弹出窗口后自动关闭）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from gui.app import Zip2EpubApp  # noqa: E402


def main() -> int:
    app = Zip2EpubApp()
    app.geometry("1280x840+60+40")
    app.attributes("-topmost", True)
    app.update()
    app._add_paths([ROOT / "sample_data"])
    app.update()

    for iid in app.tree.get_children():
        plan = app.books[iid]
        if plan.effective_mode() == "mixed":
            app.tree.selection_set(iid)
            app.on_select()
            break

    app.update()
    app.show_page(0)
    app.update()
    app.lift()
    time.sleep(0.7)
    app.update()

    def grab(name: str) -> None:
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        output = ROOT / "docs" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(output)
        print("saved", output)

    grab("gui.png")

    # 再截一张图片模式的书
    for iid in app.tree.get_children():
        if app.books[iid].effective_mode() == "images":
            app.tree.selection_set(iid)
            app.on_select()
            break
    app.update()
    app.show_page(2)
    time.sleep(0.5)
    grab("gui-comic.png")

    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
