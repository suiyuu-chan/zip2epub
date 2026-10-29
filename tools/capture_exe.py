"""用 PrintWindow 抓取 exe 窗口（不受其它窗口遮挡影响）。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def capture(hwnd: int, output: Path) -> bool:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False

    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)

    # 2 = PW_RENDERFULLCONTENT，能抓到 DWM 合成后的内容
    user32.PrintWindow(hwnd, mem_dc, 2)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    info = BITMAPINFO()
    info.bmiHeader = header

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)

    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return True


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    title = "zip2epub · 批量 ZIP 转 EPUB"
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        print("window not found")
        return 1

    output = ROOT / "docs" / "exe-gui.png"
    if capture(hwnd, output):
        print("saved", output)
        return 0
    print("capture failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
