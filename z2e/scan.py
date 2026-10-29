"""扫描 zip、归类条目、自然排序、生成可编辑的书籍计划。"""

from __future__ import annotations

import posixpath
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import unquote

from .model import (
    HTML_EXTS,
    MODE_AUTO,
    MODE_IMAGES,
    MODE_TEXT,
    SKIP_DIRS,
    SKIP_FILES,
    BookPlan,
    Entry,
    Page,
    ScanResult,
    kind_of,
)

_NUM_RE = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple:
    """自然排序：1 < 2 < 10，且 1 < 01，中文按码位稳定排序。"""
    text = unicodedata.normalize("NFC", text)
    parts = _NUM_RE.split(text.casefold())
    key: list[tuple] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            key.append((0, int(part), len(part)))
        elif part:
            key.append((1, part, 0))
    key.append((2, text, 0))
    return tuple(key)


def _is_junk(parts: list[str]) -> bool:
    lowered = [p.lower() for p in parts]
    if any(p in SKIP_DIRS for p in lowered[:-1]):
        return True
    name = lowered[-1]
    if name in SKIP_FILES or name.startswith("."):
        return True
    return False


def _entry_parts(name: str) -> list[str]:
    return [p for p in name.replace("\\", "/").split("/") if p]


def scan_zip(path: Path) -> ScanResult:
    path = Path(path)
    warnings: list[str] = []
    entries: list[Entry] = []

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"无法读取 zip：{exc}") from exc

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = _entry_parts(info.filename)
            if not parts or _is_junk(parts):
                continue
            ext = Path(info.filename).suffix
            kind = kind_of(ext)
            if kind is None:
                continue
            entries.append(Entry(name=info.filename, size=info.file_size, kind=kind))

    entries.sort(key=lambda e: natural_key(e.name))

    if not entries:
        warnings.append("zip 内没有可用的图片或文字文件")
    if not any(e.kind == "image" for e in entries):
        warnings.append("没有图片，封面将自动生成文字封面")

    return ScanResult(source=path, title=clean_title(path.stem), entries=entries, warnings=warnings)


def clean_title(raw: str) -> str:
    title = unicodedata.normalize("NFC", raw).strip()
    for junk in ("\u200b", "\ufeff"):
        title = title.replace(junk, "")
    return title or "未命名"


def _normalize_ref(ref: str) -> str:
    ref = unquote(ref.strip().split("?")[0].split("#")[0]).replace("\\", "/")
    return ref.lstrip("./").lower()


def _html_refs(archive: zipfile.ZipFile, entry: str) -> list[str]:
    """取出 HTML 里引用的图片条目名（尽力而为，仅用于避免重复成页）。"""
    try:
        raw = archive.read(entry)
    except KeyError:
        return []
    text = decode_text(raw)
    refs: list[str] = []
    for match in re.finditer(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", text, re.I):
        src = match.group(1)
        if src.startswith(("data:", "http:", "https:")):
            continue
        refs.append(src)
    base = posixpath.dirname(entry.replace("\\", "/"))
    resolved: list[str] = []
    names = {i.filename.lower(): i.filename for i in archive.infolist()}
    for src in refs:
        candidate = posixpath.normpath(posixpath.join(base, src)).replace("\\", "/")
        if candidate.lower() in names:
            resolved.append(names[candidate.lower()])
    return resolved


def plan_book(
    scan: ScanResult,
    mode: str = MODE_AUTO,
    title: str | None = None,
    author: str = "",
    cover: str | None = None,
) -> BookPlan:
    """按模式把扫描结果排成页面序列。

    - images：一图一页，按自然顺序
    - text  ：每个 txt/html 一章；HTML 内引用的图片随章节带走
    - auto  ：有文字有图 → 图文混排；只有图片 → 图片模式；没有图片 → 文字模式
    """
    resolved_mode = scan.auto_mode if mode == MODE_AUTO else mode
    pages: list[Page] = []

    with zipfile.ZipFile(scan.source) as archive:
        if resolved_mode == MODE_IMAGES:
            for index, entry in enumerate(scan.images, start=1):
                pages.append(Page(kind="image", entry=entry.name, label=f"第 {index} 页"))
        else:
            referenced: set[str] = set()
            html_entries = [e for e in scan.entries if e.kind == "html"]
            for entry in html_entries:
                referenced.update(name.lower() for name in _html_refs(archive, entry.name))

            for entry in scan.entries:
                if entry.kind == "image":
                    if entry.name.lower() in referenced:
                        continue
                    pages.append(Page(kind="image", entry=entry.name, label=f"第 {len(pages) + 1} 页"))
                elif entry.kind == "html":
                    refs = _html_refs(archive, entry.name)
                    pages.append(Page(kind="html", entry=entry.name, heading=entry.stem, images=refs))
                else:
                    pages.append(Page(kind="text", entry=entry.name, heading=entry.stem))

    plan = BookPlan(
        source=scan.source,
        title=title or scan.title,
        author=author,
        mode=mode,
        pages=pages,
        cover_entry=cover or None,
        warnings=list(scan.warnings),
        scan=scan,
    )

    if plan.cover_entry is None:
        plan.cover_entry = plan.default_cover()

    if not pages:
        plan.warnings.append("没有可排入的页面")
    return plan


def decode_text(data: bytes) -> str:
    """本地解码：BOM → UTF-8 → GB18030 → Big5 → 替换。不做任何内容处理。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")

    head = data[:4096].decode("latin-1", "ignore")
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.I)
    if match:
        declared = match.group(1)
        try:
            return data.decode(declared, "replace")
        except (LookupError, UnicodeDecodeError):
            pass

    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_entry(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def html_to_text(raw_text: str) -> str:
    """HTML → 纯文本（用于列表预览）：去掉 head/script/style，只留正文。"""
    try:
        from lxml import html as lhtml

        document = lhtml.fromstring(raw_text)
    except Exception:
        return raw_text

    for tag in document.xpath(".//script | .//style | .//head | .//noscript"):
        parent = tag.getparent()
        if parent is not None:
            parent.remove(tag)

    container = document
    if document.tag == "html":
        body = document.find(".//body")
        container = body if body is not None else document
    return " ".join(container.text_content().split())


def collect_stats(plan: BookPlan) -> dict:
    images = sum(1 for p in plan.pages if p.kind == "image")
    texts = sum(1 for p in plan.pages if p.kind in ("text", "html"))
    return {
        "pages": len(plan.pages),
        "images": images,
        "chapters": texts,
        "mode": plan.mode,
    }
