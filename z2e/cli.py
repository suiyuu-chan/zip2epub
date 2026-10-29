"""命令行入口：convert / scan / preview / sheet / gui。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .convert import ConvertOptions, collect_zips, convert_batch
from .model import MODE_AUTO, MODE_IMAGES, MODE_LABELS, MODE_TEXT
from .preview import render_contact_sheet, render_preview_file
from .scan import plan_book, scan_zip


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cmd_convert(args: argparse.Namespace) -> int:
    zips = collect_zips([Path(p) for p in args.paths], recursive=not args.no_recursive)
    if not zips:
        print("[FAIL] 没有找到任何 zip 文件")
        return 2

    options = ConvertOptions(
        output_dir=Path(args.output) if args.output else None,
        author=args.author,
        mode=args.mode,
        overwrite=args.force,
        validate=not args.no_validate,
        jobs=args.jobs,
    )

    print(f"[INFO] 共 {len(zips)} 个 zip，并行 {options.jobs} 个任务")
    started = time.time()

    def progress(done: int, total: int, result) -> None:
        if result.ok:
            print(
                f"  [{done}/{total}] OK   {result.name} -> {result.output.name} "
                f"({result.pages} 页, {_human(result.size)}, {result.seconds:.1f}s)"
            )
            for warning in result.warnings[:3]:
                print(f"         ! {warning}")
        else:
            print(f"  [{done}/{total}] FAIL {result.name}: {result.error}")

    results = convert_batch(zips, options, progress=progress)
    ok = sum(1 for r in results if r.ok)
    failed = len(results) - ok
    print(f"[DONE] 成功 {ok}，失败 {failed}，用时 {time.time() - started:.1f}s")
    if failed:
        for result in results:
            if not result.ok:
                print(f"  - {result.source.name}: {result.error}")
        return 1
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    scan = scan_zip(Path(args.zip))
    plan = plan_book(scan, mode=args.mode, author=args.author)

    if args.json:
        payload = {
            "source": str(scan.source),
            "title": plan.title,
            "mode": plan.effective_mode(),
            "mode_setting": plan.mode,
            "auto_mode": scan.auto_mode,
            "pages": [
                {"kind": p.kind, "entry": p.entry, "label": p.display(), "images": p.images}
                for p in plan.pages
            ],
            "cover": plan.cover_entry,
            "warnings": scan.warnings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"文件: {scan.source}")
    print(f"书名: {plan.title}")
    print(f"模式: {plan.mode_label()}  ·  设置: {MODE_LABELS.get(plan.mode, plan.mode)}")
    print(f"页数: {len(plan.pages)}，图片 {sum(1 for p in plan.pages if p.kind == 'image')}")
    print(f"封面: {plan.cover_entry or '（无图片，将生成文字封面）'}")
    for warning in scan.warnings:
        print(f"注意: {warning}")
    for index, page in enumerate(plan.pages, start=1):
        refs = f"  引用 {len(page.images)} 图" if page.images else ""
        print(f"  {index:3d}. [{page.kind}] {page.entry}{refs}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    scan = scan_zip(Path(args.zip))
    plan = plan_book(scan, mode=args.mode, author=args.author)
    output = render_preview_file(plan, Path(args.output), max_pages=args.max_pages)
    print(f"[OK] 预览已生成：{output}")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    scan = scan_zip(Path(args.zip))
    plan = plan_book(scan, mode=args.mode, author=args.author)
    output = render_contact_sheet(plan, Path(args.output), limit=args.limit)
    print(f"[OK] 联系表已生成：{output}")
    return 0


def cmd_gui(_: argparse.Namespace) -> int:
    from gui.app import main as gui_main

    gui_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zip2epub",
        description="批量把 zip 内的图片 / 文字转成 EPUB（纯本地，无联网，无内容审查）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--mode", choices=[MODE_AUTO, MODE_IMAGES, MODE_TEXT], default=MODE_AUTO)
        p.add_argument("--author", default="")

    convert = sub.add_parser("convert", help="批量转换 zip / 文件夹")
    convert.add_argument("paths", nargs="+", help="zip 文件或包含 zip 的文件夹")
    convert.add_argument("-o", "--output", help="输出目录（默认与 zip 同目录）")
    convert.add_argument("-j", "--jobs", type=int, default=4, help="并行任务数（默认 4）")
    convert.add_argument("--force", action="store_true", help="覆盖同名 epub（默认不覆盖）")
    convert.add_argument("--no-validate", action="store_true", help="跳过输出自检")
    convert.add_argument("--no-recursive", action="store_true", help="文件夹不递归")
    add_common(convert)
    convert.set_defaults(func=cmd_convert)

    scan = sub.add_parser("scan", help="查看某个 zip 的解析结果")
    scan.add_argument("zip")
    scan.add_argument("--json", action="store_true")
    add_common(scan)
    scan.set_defaults(func=cmd_scan)

    preview = sub.add_parser("preview", help="生成离线 HTML 预览")
    preview.add_argument("zip")
    preview.add_argument("-o", "--output", default="preview.html")
    preview.add_argument("--max-pages", type=int, default=40)
    add_common(preview)
    preview.set_defaults(func=cmd_preview)

    sheet = sub.add_parser("sheet", help="生成缩略图联系表 PNG")
    sheet.add_argument("zip")
    sheet.add_argument("-o", "--output", default="sheet.png")
    sheet.add_argument("--limit", type=int, default=16)
    add_common(sheet)
    sheet.set_defaults(func=cmd_sheet)

    gui = sub.add_parser("gui", help="打开图形界面")
    gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 输出被重定向（管道 / 文件）时统一用 UTF-8，避免中文在 UTF-8 终端里乱码；
    # 直接连控制台时交给 Python 自己的 Windows 控制台实现。
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[INFO] 已取消")
        return 130
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
