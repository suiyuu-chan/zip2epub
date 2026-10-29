"""EPUB 3 构建器：纯本地生成，不联网、不做任何内容审查或改写。"""

from __future__ import annotations

import io
import posixpath
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from lxml import etree, html as lhtml

from .model import BookPlan, Page
from .scan import decode_text

NS_OPF = "http://www.idpf.org/2007/opf"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_XHTML = "http://www.w3.org/1999/xhtml"

XHTML_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="{css}"/>
</head>
<body>
{body}
</body>
</html>
"""

STYLE_CSS = """@charset "utf-8";
html, body { margin: 0; padding: 0; }
body {
  font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", "SimSun", serif;
  line-height: 1.75;
  color: #111;
  background: #fff;
}
.page { margin: 0; padding: 0; text-align: center; page-break-after: always; }
.page img { max-width: 100%; max-height: 100vh; object-fit: contain; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100vh; }
.chapter { padding: 1.2em 1.6em 2em; }
.chapter h1 { font-size: 1.35em; margin: 0 0 1em 0; line-height: 1.4; }
.chapter p { margin: 0 0 0.65em 0; text-indent: 2em; }
.chapter img { max-width: 100%; height: auto; }
.chapter blockquote { margin: 0.8em 2em; color: #444; }
"""


class BuildError(RuntimeError):
    pass


@dataclass
class BuildReport:
    ok: bool
    output: Path | None = None
    pages: int = 0
    images: int = 0
    size: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ""


class EpubBuilder:
    """把 BookPlan 写成 EPUB 3（带 EPUB2 兼容的 ncx 与 cover meta）。"""

    def __init__(self, plan: BookPlan, output: Path, convert_images: bool = True):
        self.plan = plan
        self.output = Path(output)
        self.convert_images = convert_images
        self.warnings: list[str] = []
        self._image_map: dict[str, tuple[str, str]] = {}   # 查找键 -> (输出文件名, media type)
        self._image_outputs: dict[str, str] = {}           # 输出文件名 -> media type（去重后写 manifest）
        self._files: dict[str, bytes] = {}
        self._page_files: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ 构建
    def build(self) -> BuildReport:
        started = datetime.now()
        if not self.plan.pages:
            raise BuildError("这本书没有页面可输出")

        with zipfile.ZipFile(self.plan.source) as archive:
            self._prepare_images(archive)
            self._build_pages(archive)
            cover_name, cover_media = self._build_cover()

        self._build_nav()
        self._write_opf(cover_name, cover_media)
        self._write_container()
        self._files["style.css"] = STYLE_CSS.encode("utf-8")
        self._validate_xml()
        self._write_zip()

        return BuildReport(
            ok=True,
            output=self.output,
            pages=len(self.plan.pages),
            images=len(self._image_outputs),
            size=self.output.stat().st_size,
            seconds=(datetime.now() - started).total_seconds(),
            warnings=list(self.warnings),
        )

    # ------------------------------------------------------------- 图片处理
    def _prepare_images(self, archive: zipfile.ZipFile) -> None:
        used = self.plan.image_entries()
        for index, entry_name in enumerate(used, start=1):
            try:
                raw = archive.read(entry_name)
            except KeyError:
                self.warnings.append(f"图片缺失：{entry_name}")
                continue

            ext = Path(entry_name).suffix.lower()
            try:
                data, out_ext, media = self._normalize_image(raw, ext)
            except Exception as exc:  # 坏图不应毁掉整本书
                self.warnings.append(f"跳过无法处理的图片 {entry_name}：{exc}")
                continue

            out_name = f"img_{index:04d}{out_ext}"
            self._files[f"images/{out_name}"] = data
            self._image_outputs[out_name] = media
            self._image_map[entry_name] = (out_name, media)
            self._image_map[entry_name.lower()] = (out_name, media)
            self._image_map.setdefault(posixpath.basename(entry_name).lower(), (out_name, media))

    def _normalize_image(self, raw: bytes, ext: str) -> tuple[bytes, str, str]:
        if ext in (".jpg", ".jpeg", ".jfif"):
            return raw, ".jpg", "image/jpeg"
        if ext == ".png":
            return raw, ".png", "image/png"
        if ext == ".gif":
            return raw, ".gif", "image/gif"
        if ext == ".webp" and not self.convert_images:
            return raw, ".webp", "image/webp"

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if getattr(image, "is_animated", False):
                buffer = io.BytesIO()
                image.save(buffer, format="GIF", save_all=True)
                return buffer.getvalue(), ".gif", "image/gif"

            if image.mode in ("RGBA", "LA", "P"):
                buffer = io.BytesIO()
                image.convert("RGBA").save(buffer, format="PNG", optimize=True)
                return buffer.getvalue(), ".png", "image/png"

            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
            return buffer.getvalue(), ".jpg", "image/jpeg"

    # ------------------------------------------------------------- 页面构建
    def _build_pages(self, archive: zipfile.ZipFile) -> None:
        for index, page in enumerate(self.plan.pages, start=1):
            page_id = f"p{index:04d}"
            href = f"pages/{page_id}.xhtml"
            title = page.display()

            if page.kind == "image":
                body = self._image_page_body(page)
            elif page.kind == "html":
                body = self._html_page_body(archive, page)
            else:
                body = self._text_page_body(archive, page)

            content = XHTML_TEMPLATE.format(
                lang=self.plan.language, title=escape(title), css="../style.css", body=body
            )
            self._files[href] = content.encode("utf-8")
            self._page_files.append((page_id, href, title))

    def _image_page_body(self, page: Page) -> str:
        mapped = self._image_map.get(page.entry) or self._image_map.get(page.entry.lower())
        if not mapped:
            return '<div class="page"><p>（图片无法读取）</p></div>'
        out_name, _ = mapped
        return (
            '<div class="page">'
            f'<img src="../images/{out_name}" alt="{escape(page.display())}"/>'
            "</div>"
        )

    def _text_page_body(self, archive: zipfile.ZipFile, page: Page) -> str:
        try:
            text = decode_text(archive.read(page.entry))
        except KeyError:
            text = ""

        paragraphs = [f"<p>{escape(block)}</p>" for block in text_to_paragraphs(text)] or ["<p></p>"]
        heading = f"<h1>{escape(page.heading)}</h1>" if page.heading else ""
        return f'<div class="chapter">{heading}' + "".join(paragraphs) + "</div>"

    def _html_page_body(self, archive: zipfile.ZipFile, page: Page) -> str:
        try:
            raw = archive.read(page.entry)
        except KeyError:
            return '<div class="chapter"><p>（章节缺失）</p></div>'

        body_html = sanitize_html(decode_text(raw), page.entry, self._image_map)
        heading = f"<h1>{escape(page.heading)}</h1>" if page.heading else ""
        return f'<div class="chapter">{heading}{body_html}</div>'

    # ----------------------------------------------------------------- 封面
    def _build_cover(self) -> tuple[str, str]:
        mapped = None
        if self.plan.cover_entry:
            mapped = self._image_map.get(self.plan.cover_entry) or self._image_map.get(
                self.plan.cover_entry.lower()
            )
            if mapped is None:
                mapped = self._image_map.get(posixpath.basename(self.plan.cover_entry).lower())

        if mapped is None:
            self._files["images/cover.png"] = make_title_cover(self.plan.title, self.plan.author)
            self._image_outputs["cover.png"] = "image/png"
            mapped = ("cover.png", "image/png")
            self.warnings.append("书中没有图片，已按书名自动生成文字封面")

        out_name, media = mapped
        body = f'<div class="cover"><img src="images/{out_name}" alt="封面"/></div>'
        content = XHTML_TEMPLATE.format(lang=self.plan.language, title="封面", css="style.css", body=body)
        self._files["cover.xhtml"] = content.encode("utf-8")
        return out_name, media

    # ------------------------------------------------------------- 目录/OPF
    def _build_nav(self) -> None:
        items = [f'<li><a href="{href}">{escape(title)}</a></li>' for _, href, title in self._page_files]
        first_href = self._page_files[0][1] if self._page_files else "nav.xhtml"

        nav = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="{NS_XHTML}" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'xml:lang="{self.plan.language}" lang="{self.plan.language}">\n'
            '<head><meta charset="utf-8"/><title>目录</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            '<body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>\n'
            + "\n".join(items)
            + "\n</ol></nav>\n"
            '<nav epub:type="landmarks" hidden="hidden"><ol>'
            '<li><a epub:type="cover" href="cover.xhtml">封面</a></li>'
            '<li><a epub:type="toc" href="nav.xhtml">目录</a></li>'
            f'<li><a epub:type="bodymatter" href="{first_href}">正文</a></li>'
            "</ol></nav>\n</body></html>\n"
        )
        self._files["nav.xhtml"] = nav.encode("utf-8")

        nav_points = [
            f'<navPoint id="np{order}" playOrder="{order}">'
            f"<navLabel><text>{escape(title)}</text></navLabel>"
            f'<content src="{href}"/></navPoint>'
            for order, (_, href, title) in enumerate(self._page_files, start=1)
        ]
        ncx = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'<head><meta name="dtb:uid" content="{self._book_id()}"/>'
            '<meta name="dtb:depth" content="1"/></head>\n'
            f"<docTitle><text>{escape(self.plan.title)}</text></docTitle>\n"
            "<navMap>\n" + "\n".join(nav_points) + "\n</navMap>\n</ncx>\n"
        )
        self._files["toc.ncx"] = ncx.encode("utf-8")

    def _book_id(self) -> str:
        seed = "zip2epub:" + str(self.plan.source.resolve())
        return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}"

    def _write_opf(self, cover_name: str, cover_media: str) -> None:
        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="css" href="style.css" media-type="text/css"/>',
            '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
            f'<item id="cover-image" href="images/{cover_name}" media-type="{cover_media}" properties="cover-image"/>',
        ]

        for out_name, media in self._image_outputs.items():
            if out_name == cover_name:
                continue
            manifest.append(f'<item id="img-{Path(out_name).stem}" href="images/{out_name}" media-type="{media}"/>')

        for page_id, href, _ in self._page_files:
            manifest.append(f'<item id="{page_id}" href="{href}" media-type="application/xhtml+xml"/>')

        spine = ['<itemref idref="cover"/>'] + [f'<itemref idref="{page_id}"/>' for page_id, _, _ in self._page_files]

        creator = f"<dc:creator>{escape(self.plan.author.strip())}</dc:creator>" if self.plan.author.strip() else ""
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<package xmlns="{NS_OPF}" version="3.0" unique-identifier="bookid" xml:lang="{self.plan.language}">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{self._book_id()}</dc:identifier>\n'
            f"    <dc:title>{escape(self.plan.title)}</dc:title>\n"
            f"    <dc:language>{self.plan.language}</dc:language>\n"
            f"    {creator}\n"
            f'    <meta property="dcterms:modified">{modified}</meta>\n'
            '    <meta name="cover" content="cover-image"/>\n'
            "  </metadata>\n"
            "  <manifest>\n    " + "\n    ".join(manifest) + "\n  </manifest>\n"
            '  <spine toc="ncx">\n    ' + "\n    ".join(spine) + "\n  </spine>\n"
            "</package>\n"
        )
        self._files["content.opf"] = opf.encode("utf-8")

    def _write_container(self) -> None:
        container = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n"
        )
        self._files["META-INF/container.xml"] = container.encode("utf-8")

    def _validate_xml(self) -> None:
        required = ("content.opf", "nav.xhtml", "toc.ncx", "cover.xhtml")
        for name in required:
            if name not in self._files:
                raise BuildError(f"缺少必需文件：{name}")

        for name, data in self._files.items():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                try:
                    etree.fromstring(data)
                except etree.XMLSyntaxError as exc:
                    raise BuildError(f"{name} 不是合法 XML：{exc}") from exc

    def _write_zip(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temp = self.output.with_suffix(".epub.tmp")
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, b"application/epub+zip")

            ordered = ["META-INF/container.xml", "content.opf"]
            ordered += [name for name in self._files if name not in ordered]
            for name in ordered:
                if name in self._files:
                    arcname = name if name.startswith("META-INF/") else "OEBPS/" + name
                    archive.writestr(arcname, self._files[name])

        temp.replace(self.output)


# --------------------------------------------------------------------- 工具


def escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _join_cjk(left: str, right: str) -> str:
    if not left or not right:
        return left + right
    if left[-1].isascii() and right[0].isascii():
        return left + " " + right
    return left + right


def text_to_paragraphs(text: str) -> list[str]:
    """把纯文本切成段落：优先空行分隔，否则按单行；中文软换行不插空格。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        blocks = [b for b in text.split("\n") if b.strip()]

    paragraphs: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        merged = lines[0]
        for line in lines[1:]:
            merged = _join_cjk(merged, line)
        paragraphs.append(merged)
    return paragraphs


def _resolve_src(src: str, entry_name: str) -> str:
    path = unquote(src.strip().split("?")[0].split("#")[0]).replace("\\", "/")
    base = posixpath.dirname(entry_name.replace("\\", "/"))
    return posixpath.normpath(posixpath.join(base, path)).lower()


def sanitize_html(raw_text: str, entry_name: str, image_map: dict[str, tuple[str, str]]) -> str:
    """把外部 HTML 变成 EPUB 可用的 XHTML 片段。

    只做技术性清理（脚本/样式/事件属性、图片路径重写），不改写任何文字内容。
    """
    try:
        document = lhtml.fromstring(raw_text)
    except (etree.ParserError, ValueError):
        return f"<pre>{escape(raw_text)}</pre>"

    container = document
    if document.tag == "html":
        body = document.find(".//body")
        container = body if body is not None else document

    for tag in container.xpath(".//script | .//style | .//iframe | .//object | .//embed | .//noscript | .//link | .//meta"):
        parent = tag.getparent()
        if parent is not None:
            parent.remove(tag)

    for element in container.iter():
        if not isinstance(element.tag, str):
            continue
        for attr in list(element.attrib):
            if attr.lower().startswith("on"):
                del element.attrib[attr]

    for image in container.xpath(".//img"):
        src = image.get("src") or ""
        if src.startswith("data:"):
            continue
        mapped = image_map.get(_resolve_src(src, entry_name)) or image_map.get(
            posixpath.basename(unquote(src)).lower()
        )
        if mapped:
            image.set("src", "../images/" + mapped[0])
        else:
            parent = image.getparent()
            if parent is not None:
                parent.remove(image)

    for element in container.xpath(".//a[@href]"):
        href = element.get("href") or ""
        if href.lower().startswith(("javascript:", "http:", "https:")):
            del element.attrib["href"]

    if document.tag in ("html", "body") or container is not document:
        parts: list[str] = []
        if container.text and container.text.strip():
            parts.append(escape(container.text.strip()))
        for child in container:
            if isinstance(child.tag, str):
                parts.append(etree.tostring(child, method="xml", encoding="unicode"))
        return "".join(parts) or "<p></p>"

    return etree.tostring(document, method="xml", encoding="unicode")


def make_title_cover(title: str, author: str, size: tuple[int, int] = (1200, 1800)) -> bytes:
    """没有图片时，用书名生成一张纯文字封面（本地绘制，不联网）。"""
    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    image = Image.new("RGB", size, (18, 22, 30))
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(int(18 + 26 * ratio), int(22 + 18 * ratio), int(30 + 46 * ratio)),
        )

    font = None
    for path in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font = ImageFont.truetype(path, 88)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    max_width = width - 200
    lines: list[str] = []
    current = ""
    for char in title:
        candidate = current + char
        if draw.textlength(candidate, font=font) > max_width and current:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)

    line_height = 118
    start_y = (height - len(lines) * line_height) // 2 - 40
    for index, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        draw.text(((width - line_width) / 2, start_y + index * line_height), line, font=font, fill=(240, 244, 250))

    if author.strip():
        try:
            small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 42)
        except OSError:
            small = font
        author_width = draw.textlength(author, font=small)
        draw.text(((width - author_width) / 2, height - 220), author, font=small, fill=(150, 162, 180))

    draw.line([(width / 2 - 60, height - 150), (width / 2 + 60, height - 150)], fill=(91, 140, 255), width=6)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------------------------------------------ EPUB 自检


def validate_epub(path: Path) -> list[str]:
    """轻量自检：结构、mimetype、OPF、引用完整性。返回问题列表。"""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or infos[0].filename != "mimetype":
                problems.append("mimetype 不是第一个条目")
            elif infos[0].compress_type != zipfile.ZIP_STORED:
                problems.append("mimetype 被压缩了")
            elif archive.read("mimetype") != b"application/epub+zip":
                problems.append("mimetype 内容不对")

            names = set(archive.namelist())
            for required in ("META-INF/container.xml", "OEBPS/content.opf"):
                if required not in names:
                    problems.append(f"缺少 {required}")

            opf = etree.fromstring(archive.read("OEBPS/content.opf"))
            for item in opf.findall(f".//{{{NS_OPF}}}item"):
                href = item.get("href")
                if href and f"OEBPS/{href}" not in names:
                    problems.append(f"manifest 指向不存在的文件：{href}")

            if not opf.findall(f".//{{{NS_OPF}}}itemref"):
                problems.append("spine 为空")
            if opf.find(f".//{{{NS_OPF}}}item[@properties='nav']") is None:
                problems.append("缺少 nav 文档")
            titles = opf.findall(f".//{{{NS_DC}}}title")
            if not titles or not (titles[0].text or "").strip():
                problems.append("缺少 dc:title")
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        problems.append(f"无法解析：{exc}")
    return problems
