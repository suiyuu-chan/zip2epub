"""按指定尺寸截图，用于检查窄窗口布局。"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from capture_exe import capture  # noqa: E402

from gui.app import Zip2EpubApp  # noqa: E402


def shoot(app: Zip2EpubApp, width: int, height: int, output: Path) -> None:
    app.geometry(f"{width}x{height}+40+40")
    app.update()
    app.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(app.winfo_id())
    capture(hwnd, output)
    print("saved", output, app.winfo_width(), app.winfo_height())


def main() -> int:
    app = Zip2EpubApp()
    app.update()
    shoot(app, 900, 600, ROOT / "docs" / "gui-narrow.png")
    shoot(app, 1280, 860, ROOT / "docs" / "gui.png")
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
