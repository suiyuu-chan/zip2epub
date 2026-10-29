"""生成 exe 图标：深色圆角方块 + 书页 + 装订线。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "app.ico"


def rounded(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = size * 0.04
    radius = size * 0.24
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(28, 34, 48, 255),
    )

    # 书页
    left = size * 0.26
    top = size * 0.22
    right = size * 0.74
    bottom = size * 0.78
    draw.rounded_rectangle([left, top, right, bottom], radius=size * 0.05, fill=(245, 247, 252, 255))

    # 装订线
    spine_x = left + (right - left) * 0.34
    draw.rectangle([spine_x, top, spine_x + size * 0.035, bottom], fill=(91, 140, 255, 255))

    # 文字行
    line_color = (150, 160, 178, 255)
    y = top + size * 0.12
    for index in range(4):
        x0 = spine_x + size * 0.10
        x1 = right - size * 0.09 - (size * 0.06 if index == 3 else 0)
        draw.rounded_rectangle([x0, y, x1, y + size * 0.035], radius=size * 0.018, fill=line_color)
        y += size * 0.12

    return image


def main() -> int:
    sizes = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]
    master = rounded(256)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    master.save(OUT, format="ICO", sizes=sizes)
    print("saved", OUT, OUT.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
