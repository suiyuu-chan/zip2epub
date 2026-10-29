"""核心数据模型：扫描结果、页面、书籍计划。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#  EPUB 阅读器普遍支持的图片格式
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif",
}
TEXT_EXTS = {".txt", ".md", ".markdown"}
HTML_EXTS = {".html", ".htm", ".xhtml"}

# 这些都按“技术垃圾”跳过，不做任何内容判断
SKIP_DIRS = {"__macosx"}
SKIP_FILES = {".ds_store", "thumbs.db", "desktop.ini"}

MODE_AUTO = "auto"
MODE_IMAGES = "images"
MODE_TEXT = "text"

MODE_LABELS = {
    MODE_AUTO: "自动识别",
    MODE_IMAGES: "图片（一页一图）",
    MODE_TEXT: "文字 / 图文混排",
}

MODE_SHORT = {
    "images": "图片",
    "text": "文字",
    "mixed": "图文混排",
}


def kind_of(ext: str) -> str | None:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    if ext in HTML_EXTS:
        return "html"
    return None


@dataclass(frozen=True)
class Entry:
    """zip 内的一个可用条目。"""

    name: str
    size: int
    kind: str  # image / text / html

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def stem(self) -> str:
        return Path(self.name).stem


@dataclass
class ScanResult:
    """一次扫描的结果（只读，不含用户编辑）。"""

    source: Path
    title: str
    entries: list[Entry]
    warnings: list[str] = field(default_factory=list)

    @property
    def images(self) -> list[Entry]:
        return [e for e in self.entries if e.kind == "image"]

    @property
    def texts(self) -> list[Entry]:
        return [e for e in self.entries if e.kind in ("text", "html")]

    @property
    def auto_mode(self) -> str:
        has_images = bool(self.images)
        has_text = bool(self.texts)
        if has_images and has_text:
            return "mixed"
        if has_text:
            return MODE_TEXT
        return MODE_IMAGES


@dataclass
class Page:
    """计划中的一页：图片页或文字/HTML 章节。"""

    kind: str  # image / text / html
    entry: str
    heading: str = ""
    label: str = ""
    images: list[str] = field(default_factory=list)  # HTML 章节内引用的图片条目

    def display(self) -> str:
        if self.kind == "image":
            return self.label or Path(self.entry).name
        return self.heading or Path(self.entry).stem


@dataclass
class BookPlan:
    """可被用户编辑、最终交给 EPUB 构建器的计划。"""

    source: Path
    title: str
    author: str = ""
    mode: str = MODE_AUTO
    pages: list[Page] = field(default_factory=list)
    cover_entry: str | None = None
    language: str = "zh"
    warnings: list[str] = field(default_factory=list)
    scan: ScanResult | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def effective_mode(self) -> str:
        """实际生效的模式：auto 时用扫描结果推断。"""
        if self.mode != MODE_AUTO:
            return self.mode
        if self.scan is not None:
            return self.scan.auto_mode
        return MODE_IMAGES

    def mode_label(self) -> str:
        return MODE_SHORT.get(self.effective_mode(), self.effective_mode())

    @property
    def image_pages(self) -> list[Page]:
        return [p for p in self.pages if p.kind == "image"]

    def image_entries(self) -> list[str]:
        """本书需要用到的全部图片（含 HTML 章节内引用的）。"""
        names: list[str] = []
        seen: set[str] = set()
        for page in self.pages:
            if page.kind == "image" and page.entry.lower() not in seen:
                seen.add(page.entry.lower())
                names.append(page.entry)
            for ref in page.images:
                if ref.lower() not in seen:
                    seen.add(ref.lower())
                    names.append(ref)
        if self.cover_entry and self.cover_entry.lower() not in seen:
            names.append(self.cover_entry)
        return names

    def default_cover(self) -> str | None:
        for page in self.pages:
            if page.kind == "image":
                return page.entry
        for page in self.pages:
            if page.images:
                return page.images[0]
        return None

    def move_page(self, index: int, delta: int) -> bool:
        target = index + delta
        if not (0 <= index < len(self.pages)) or not (0 <= target < len(self.pages)):
            return False
        self.pages[index], self.pages[target] = self.pages[target], self.pages[index]
        return True
