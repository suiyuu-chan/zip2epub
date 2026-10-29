"""批量转换引擎：多 zip 并行，进度回调，不会因为单本失败中断整批。"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .epub import EpubBuilder, validate_epub
from .model import MODE_AUTO, BookPlan
from .scan import plan_book, scan_zip

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ConvertOptions:
    output_dir: Path | None = None
    author: str = ""
    mode: str = MODE_AUTO
    convert_images: bool = True
    overwrite: bool = True
    validate: bool = True
    jobs: int = 4


@dataclass
class ConvertResult:
    source: Path
    output: Path | None
    ok: bool
    error: str = ""
    pages: int = 0
    images: int = 0
    size: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.source.stem


def safe_filename(name: str) -> str:
    cleaned = INVALID_CHARS.sub("_", name).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "未命名"


def _unique(path: Path, taken: set[Path] | None = None) -> Path:
    taken = taken if taken is not None else set()
    candidate = path
    index = 2
    while candidate.exists() or candidate in taken:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        index += 1
    taken.add(candidate)
    return candidate


def build_plan(zip_path: Path, options: ConvertOptions) -> BookPlan:
    scan = scan_zip(Path(zip_path))
    return plan_book(scan, mode=options.mode, author=options.author)


def convert_one(
    zip_path: Path,
    options: ConvertOptions,
    plan: BookPlan | None = None,
    output: Path | None = None,
) -> ConvertResult:
    zip_path = Path(zip_path)
    started = time.time()
    try:
        plan = plan or build_plan(zip_path, options)
        out_dir = Path(output).parent if output else (options.output_dir or zip_path.parent)
        target = Path(output) if output else out_dir / (safe_filename(plan.title) + ".epub")
        if output is None and not options.overwrite and target.exists():
            target = _unique(target)

        report = EpubBuilder(plan, target, convert_images=options.convert_images).build()
        warnings = list(report.warnings)
        if options.validate:
            warnings.extend(validate_epub(target))

        return ConvertResult(
            source=zip_path,
            output=target,
            ok=True,
            pages=report.pages,
            images=report.images,
            size=report.size,
            seconds=time.time() - started,
            warnings=warnings,
        )
    except Exception as exc:  # 单本失败不拖垮整批
        return ConvertResult(
            source=zip_path,
            output=None,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            seconds=time.time() - started,
        )


def convert_batch(
    zips: list[Path],
    options: ConvertOptions,
    progress: Callable[[int, int, ConvertResult], None] | None = None,
    cancel: threading.Event | None = None,
    plans: dict[Path, BookPlan] | None = None,
) -> list[ConvertResult]:
    """并行转换。progress 在完成一本时回调（可能来自工作线程）。"""
    options.jobs = max(1, min(options.jobs, 16))
    out_dir = Path(options.output_dir) if options.output_dir else None

    # 先排定输出路径，避免同名 zip 并行时互相覆盖
    taken: set[Path] = set()
    tasks: list[tuple[Path, Path]] = []
    for zip_path in zips:
        zip_path = Path(zip_path)
        folder = out_dir or zip_path.parent
        title = plans.get(zip_path).title if plans and zip_path in plans else safe_filename(zip_path.stem)
        target = folder / (safe_filename(title) + ".epub")
        if not options.overwrite:
            target = _unique(target, taken)
        elif target in taken:
            target = _unique(target, taken)
        else:
            taken.add(target)
        tasks.append((zip_path, target))

    results: list[ConvertResult] = []
    total = len(tasks)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
        futures = {
            pool.submit(convert_one, zip_path, options, plans.get(zip_path) if plans else None, target): zip_path
            for zip_path, target in tasks
        }
        for done, future in enumerate(as_completed(futures), start=1):
            if cancel is not None and cancel.is_set():
                break
            try:
                result = future.result()
            except Exception as exc:
                result = ConvertResult(futures[future], None, False, error=str(exc))
            results.append(result)
            if progress:
                progress(done, total, result)

    if cancel is not None and cancel.is_set():
        for future in futures:
            future.cancel()

    return results


def collect_zips(paths: list[Path], recursive: bool = True) -> list[Path]:
    """从文件/文件夹中收集 zip（大小写不敏感）。"""
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*.zip" if recursive else "*.zip"
            candidates = list(path.glob(pattern)) + list(path.glob(pattern.replace(".zip", ".ZIP")))
        elif path.is_file() and path.suffix.lower() == ".zip":
            candidates = [path]
        else:
            candidates = []

        for item in candidates:
            resolved = item.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(item)

    return sorted(found, key=lambda p: str(p).casefold())
