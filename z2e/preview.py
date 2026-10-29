"""预览：单文件 HTML（内嵌图片，无需联网）与缩略图联系表 PNG。"""

from __future__ import annotations

import base64
import html
import io
import zipfile
from pathlib import Path

from .epub import escape, sanitize_html, text_to_paragraphs
from .model import BookPlan, Page
from .scan import decode_text, html_to_text


def _data_uri(raw: bytes, name: str) -> str:
    ext = Path(name).suffix.lower()
    media = {
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")
    return f"data:{media};base64," + base64.b64encode(raw).decode("ascii")


def _image_uris(plan: BookPlan) -> dict[str, str]:
    """条目名（小写）→ data URI。"""
    uris: dict[str, str] = {}
    with zipfile.ZipFile(plan.source) as archive:
        for entry in plan.image_entries():
            try:
                raw = archive.read(entry)
            except KeyError:
                continue
            uri = _data_uri(raw, entry)
            uris[entry.lower()] = uri
            uris.setdefault(Path(entry).name.lower(), uri)
    return uris


def build_preview_html(plan: BookPlan, max_pages: int | None = None) -> str:
    """生成一个完全自包含的 HTML 预览：不引用任何外部资源。"""
    uris = _image_uris(plan)
    pages_html: list[str] = []
    pages = plan.pages if max_pages is None else plan.pages[:max_pages]

    with zipfile.ZipFile(plan.source) as archive:
        for index, page in enumerate(pages, start=1):
            label = f"第 {index} 页 / 共 {len(plan.pages)} 页"
            if page.kind == "image":
                uri = uris.get(page.entry.lower()) or uris.get(Path(page.entry).name.lower()) or ""
                inner = (
                    f'<div class="sheet"><img src="{uri}" alt=""/>'
                    f'<div class="cap">{html.escape(page.display())}</div></div>'
                    if uri
                    else '<div class="sheet missing">图片无法读取</div>'
                )
            elif page.kind == "html":
                try:
                    raw = archive.read(page.entry)
                except KeyError:
                    raw = b""
                body = sanitize_html(decode_text(raw), page.entry, {})
                body = _replace_image_srcs(body, uris)
                inner = f'<div class="sheet text"><h3>{html.escape(page.heading)}</h3>{body}</div>'
            else:
                try:
                    text = decode_text(archive.read(page.entry))
                except KeyError:
                    text = ""
                paras = "".join(f"<p>{html.escape(p)}</p>" for p in text_to_paragraphs(text))
                inner = f'<div class="sheet text"><h3>{html.escape(page.heading)}</h3>{paras}</div>'

            pages_html.append(
                f'<section class="page" id="p{index}"><div class="meta">{html.escape(label)}'
                f'<span class="kind">{"图片" if page.kind == "image" else "文字"}</span></div>{inner}</section>'
            )

    note = ""
    if max_pages is not None and len(plan.pages) > max_pages:
        note = f'<p class="note">预览只显示前 {max_pages} 页，共 {len(plan.pages)} 页。</p>'

    author = f'<div class="author">{html.escape(plan.author)}</div>' if plan.author.strip() else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(plan.title)} · 预览</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0f1115; color: #e6e9f0;
         font: 15px/1.7 "Microsoft YaHei UI", "Segoe UI", sans-serif; }}
  header {{ padding: 28px 32px 18px; border-bottom: 1px solid #232838; position: sticky; top: 0;
           background: rgba(15,17,21,.92); backdrop-filter: blur(8px); z-index: 5; }}
  h1 {{ margin: 0; font-size: 22px; }}
  .author {{ color: #9aa5b8; font-size: 13px; margin-top: 4px; }}
  .badge {{ display: inline-block; margin-left: 10px; padding: 2px 8px; border-radius: 6px;
           background: #1b2740; color: #7ea2ff; font-size: 12px; vertical-align: middle; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 24px 20px 80px; }}
  .page {{ margin: 0 0 28px; }}
  .meta {{ display: flex; justify-content: space-between; color: #6c7686; font-size: 12px; margin-bottom: 8px; }}
  .kind {{ color: #7ea2ff; }}
  .sheet {{ background: #fff; border-radius: 10px; padding: 18px; color: #111; }}
  .sheet img {{ display: block; max-width: 100%; margin: 0 auto; }}
  .sheet .cap {{ text-align: center; color: #667; font-size: 12px; margin-top: 8px; }}
  .sheet.text h3 {{ margin: 0 0 12px; font-size: 18px; }}
  .sheet.text p {{ text-indent: 2em; margin: 0 0 .6em; }}
  .sheet.text img {{ max-width: 100%; height: auto; margin: 12px auto; }}
  .missing {{ color: #b00; text-align: center; }}
  .note {{ color: #9aa5b8; text-align: center; font-size: 13px; }}
  footer {{ text-align: center; color: #55607a; font-size: 12px; padding: 20px; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(plan.title)}<span class="badge">{html.escape(plan.mode)}</span></h1>
  {author}
</header>
<main>
{''.join(pages_html) if pages_html else '<p class="note">这本书还没有页面。</p>'}
{note}
</main>
<footer>由 zip2epub 生成的离线预览 · 共 {len(plan.pages)} 页</footer>
</body>
</html>"""


def _replace_image_srcs(body: str, uris: dict[str, str]) -> str:
    import re

    def repl(match: "re.Match[str]") -> str:
        name = match.group(1).lower()
        return 'src="' + uris.get(name, uris.get(Path(name).name.lower(), "")) + '"'

    return re.sub(r'src="\.\./images/([^"]+)"', repl, body)


def render_preview_file(plan: BookPlan, output: Path, max_pages: int | None = 40) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_preview_html(plan, max_pages=max_pages), encoding="utf-8")
    return output


def render_contact_sheet(plan: BookPlan, output: Path, columns: int = 4, limit: int = 16) -> Path:
    """把前若干页画成一张 PNG 联系表，用于快速核对顺序与封面。"""
    from PIL import Image, ImageDraw, ImageFont
    output = Path(output)
    cell_w, cell_h = 300, 400
    gap = 18
    header_h = 96
    rows = max(1, (min(limit, len(plan.pages)) + columns - 1) // columns)
    width = columns * cell_w + (columns + 1) * gap
    height = header_h + rows * (cell_h + 52) + gap

    canvas = Image.new("RGB", (width, height), (15, 17, 21))
    draw = ImageDraw.Draw(canvas)

    def font(size: int, bold: bool = False):
        for path in (
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def wrap(text: str, text_font, max_width: int, max_lines: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            if char == "\n":
                lines.append(current)
                current = ""
                if len(lines) >= max_lines:
                    break
                continue
            if draw.textlength(current + char, font=text_font) > max_width and current:
                lines.append(current)
                current = char
                if len(lines) >= max_lines:
                    break
            else:
                current += char
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines[:max_lines]

    title_font = font(30, bold=True)
    small_font = font(15)
    body_font = font(14)
    cap_font = font(14)

    draw.text((gap, 26), plan.title, font=title_font, fill=(230, 236, 246))
    subtitle = f"{len(plan.pages)} 页 · {plan.mode_label()}"
    if plan.author.strip():
        subtitle += f" · {plan.author}"
    draw.text((gap, 66), subtitle, font=small_font, fill=(150, 162, 184))

    pages = plan.pages[:limit]
    with zipfile.ZipFile(plan.source) as archive:
        for index, page in enumerate(pages):
            row, col = divmod(index, columns)
            x = gap + col * (cell_w + gap)
            y = header_h + row * (cell_h + 52)

            draw.rounded_rectangle([x, y, x + cell_w, y + cell_h], radius=10, fill=(255, 255, 255))

            if page.kind == "image":
                try:
                    image = Image.open(io.BytesIO(archive.read(page.entry)))
                    image.load()
                    image = image.convert("RGB")
                    image.thumbnail((cell_w - 20, cell_h - 20))
                    canvas.paste(image, (x + (cell_w - image.width) // 2, y + (cell_h - image.height) // 2))
                except Exception:
                    draw.text((x + 16, y + 16), "图片无法读取", font=small_font, fill=(180, 60, 60))
            else:
                try:
                    raw = archive.read(page.entry)
                    raw_text = decode_text(raw)
                except KeyError:
                    raw_text = ""

                if page.kind == "html":
                    try:
                        raw_text = html_to_text(raw_text)
                    except Exception:
                        pass

                body = " ".join(raw_text.split())
                draw.text((x + 16, y + 16), (page.heading or "文字页")[:16],
                          font=font(18, bold=True), fill=(20, 20, 30))
                lines = wrap(body, body_font, cell_w - 32, 15)
                draw.multiline_text((x + 16, y + 54), "\n".join(lines), font=body_font,
                                    fill=(48, 48, 60), spacing=7)

            if plan.cover_entry and page.entry == plan.cover_entry:
                badge_x = x + cell_w - 78
                draw.rounded_rectangle([badge_x, y + 12, badge_x + 62, y + 36], radius=6, fill=(91, 140, 255))
                draw.text((badge_x + 12, y + 15), "封面", font=cap_font, fill=(255, 255, 255))

            caption = f"#{index + 1}  {page.display()}"
            draw.text((x + 2, y + cell_h + 12), caption[:40], font=cap_font, fill=(140, 150, 168))

    canvas.save(output)
    return output
