"""生成演示用 zip 样本（本地合成图片与文字，不联网）。"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT = r"C:\Windows\Fonts\msyh.ttc"


def font(size: int, bold: bool = False):
    for path in (FONT_BOLD if bold else FONT, FONT, r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def panel(index: int, total: int, title: str, size=(900, 1300)) -> bytes:
    width, height = size
    base = (24 + index * 9 % 120, 40 + index * 17 % 110, 80 + index * 23 % 120)
    image = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(int(base[0] + 30 * ratio), int(base[1] + 20 * ratio), int(base[2] + 40 * ratio)),
        )

    draw.rectangle([40, 40, width - 40, height - 40], outline=(240, 240, 250), width=3)
    draw.text((60, 70), title, font=font(38, bold=True), fill=(245, 247, 252))
    draw.text((60, 130), f"第 {index} / {total} 页", font=font(26), fill=(215, 222, 235))

    big = font(220, bold=True)
    text = str(index)
    box = draw.textbbox((0, 0), text, font=big)
    draw.text(((width - (box[2] - box[0])) / 2, height / 2 - 140), text, font=big, fill=(255, 255, 255))

    draw.text((60, height - 90), "zip2epub 演示素材", font=font(24), fill=(200, 208, 222))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def make_comic() -> None:
    files = {}
    total = 10
    for index in range(1, total + 1):
        files[f"第1话/{index:02d}.png"] = panel(index, total, "示例漫画 · 第 1 话")
    write_zip(OUT / "漫画示例.zip", files)


def make_text() -> None:
    chapters = {
        "01_开端.txt": "第一章 开端\n\n夜色像一层薄薄的墨，铺在旧城区的屋顶上。\n他推开吱呀作响的木门，走进了那间没有招牌的钟表铺。\n\n柜台上摆着一只停摆的怀表，指针停在三点十七分。\n",
        "02_钟表铺.txt": "第二章 钟表铺\n\n老人没有抬头，只是用镊子夹起一枚细小的齿轮。\n“你来得比约定的时间早了七分钟。”\n\n窗外的雨开始落下来，敲在玻璃上，像无数细小的秒针。\n",
        "03_停摆.txt": "第三章 停摆\n\n三点十七分，是这座城市所有时钟同时停下的时刻。\n没有人记得那一分钟里发生了什么。\n",
    }
    files = {name: content.encode("utf-8") for name, content in chapters.items()}
    # 故意放一个 GB18030 编码的章节，验证本地解码
    files["04_附录.txt"] = "附录：关于时区与钟表的小记\n\n本地时间并不总是均匀的。\n".encode("gb18030")
    write_zip(OUT / "纯文字示例.zip", files)


def make_mixed() -> None:
    illustration = panel(1, 1, "插图")
    illustration2 = panel(2, 1, "插图二")
    chapter1 = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>第一章</title>
<style>body{background:#fff}</style></head>
<body>
<h2>第一章 · 雨夜</h2>
<p>雨落在青石板上，声音细碎而绵长。</p>
<p><img src="images/scene1.png" alt="雨夜"/></p>
<p>他抬起头，看见钟楼的轮廓在雨幕里忽明忽暗。</p>
<script>console.log('这段脚本会被移除');</script>
<p>街角的咖啡店还亮着灯，玻璃上蒙着一层白雾。</p>
</body></html>"""
    chapter2 = """<html><head><meta charset="utf-8"></head><body>
<h2>第二章 · 钟楼</h2>
<p>钟楼的台阶有九十九级，他数到第八十级时停了下来。</p>
<p><img src="images/scene2.png"/></p>
<p>风从塔顶灌下来，带着铁锈和旧纸的味道。</p>
</body></html>"""
    files = {
        "chapter01.html": chapter1.encode("utf-8"),
        "chapter02.html": chapter2.encode("utf-8"),
        "images/scene1.png": illustration,
        "images/scene2.png": illustration2,
        "images/extra.png": panel(3, 1, "独立插图"),
        "notes.txt": "创作手记\n\n这一章写于雨夜。\n".encode("utf-8"),
    }
    write_zip(OUT / "图文混排示例.zip", files)


def make_webp() -> None:
    files = {}
    for index in range(1, 5):
        image = Image.open(io.BytesIO(panel(index, 4, "WebP 示例")))
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=85)
        files[f"page{index}.webp"] = buffer.getvalue()
    write_zip(OUT / "webp示例.zip", files)


def make_broken() -> None:
    write_zip(OUT / "非图片非文字.zip", {"readme.dat": b"nothing useful", "data.bin": b"\x00\x01"})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    make_comic()
    make_text()
    make_mixed()
    make_webp()
    make_broken()
    for path in sorted(OUT.glob("*.zip")):
        print(f"{path.name}: {path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
