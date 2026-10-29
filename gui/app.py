"""zip2epub 图形界面：批量队列 + 单本编辑 + 页面预览。

只依赖标准库 tkinter 与 Pillow；全程本地运行，不发起任何网络请求。
"""

from __future__ import annotations

import copy
import ctypes
import io
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageTk  # noqa: E402

from z2e.convert import ConvertOptions, collect_zips, convert_batch  # noqa: E402
from z2e.model import MODE_AUTO, MODE_IMAGES, MODE_LABELS, MODE_TEXT, BookPlan  # noqa: E402
from z2e.preview import render_preview_file  # noqa: E402
from z2e.scan import decode_text, html_to_text, plan_book, scan_zip  # noqa: E402

COLORS = {
    "bg": "#12151b",
    "panel": "#1a1e27",
    "panel2": "#222836",
    "border": "#2b3342",
    "fg": "#e7ebf3",
    "fg2": "#9aa5b8",
    "fg3": "#6c7686",
    "accent": "#5b8cff",
    "ok": "#34d399",
    "warn": "#f5a524",
    "err": "#f87171",
}
FONT_UI = "Microsoft YaHei UI"

MODE_CHOICES = [MODE_LABELS[MODE_AUTO], MODE_LABELS[MODE_IMAGES], MODE_LABELS[MODE_TEXT]]
MODE_VALUES = [MODE_AUTO, MODE_IMAGES, MODE_TEXT]


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def data_dir() -> Path:
    """可写目录：源码运行时用项目根目录，打包成 exe 后用 %LOCALAPPDATA%\\zip2epub。"""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "zip2epub"
    else:
        base = ROOT
    base.mkdir(parents=True, exist_ok=True)
    return base


class Zip2EpubApp(tk.Tk):
    def __init__(self) -> None:
        enable_dpi_awareness()
        super().__init__()

        self.title("zip2epub · 批量 ZIP 转 EPUB")
        self.geometry(self._initial_geometry())
        self.minsize(900, 600)
        self.configure(bg=COLORS["bg"])

        self.books: dict[str, BookPlan] = {}
        self.current_key: str | None = None
        self.preview_index = 0
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last_dir = str(Path.home())
        self._last_zip_dir: Path | None = None
        self._output_auto = tk.BooleanVar(value=True)
        self._jobs = tk.IntVar(value=4)
        self._busy = False
        self._loading = False
        self._cover_values: list[str | None] = []

        self._init_style()
        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self.after(120, self._poll_queue)
        self.after(60, self._apply_dark_title_bar)

    def _initial_geometry(self) -> str:
        """按屏幕大小取一个放得下的初始尺寸，避免窗口超出屏幕导致右侧按钮点不到。"""
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = max(900, min(1280, int(screen_w * 0.86)))
        height = max(600, min(860, int(screen_h * 0.84)))
        left = max(0, (screen_w - width) // 2)
        top = max(0, (screen_h - height) // 3)
        return f"{width}x{height}+{left}+{top}"

    def _bind_shortcuts(self) -> None:
        """窗口级的快捷键，不依赖焦点落在哪个控件上。"""
        self.bind_all("<Control-o>", lambda _e: self.add_files())
        self.bind_all("<Control-O>", lambda _e: self.add_folder())
        self.bind_all("<Control-Return>", lambda _e: self.start_convert())
        self.bind_all("<Delete>", self._on_delete_key)

    def _on_delete_key(self, _event=None):
        # 只有焦点在书单上时才删书，避免在输入框/预览里按 Delete 误删
        if self.focus_get() is self.tree:
            self.remove_selected()
            return "break"
        return None

    def _on_pane_release(self, _event=None) -> None:
        self.after(10, self._clamp_sash)

    def _on_window_configure(self, event) -> None:
        if event.widget is self:
            self.after(10, self._clamp_sash)

    def _clamp_sash(self) -> None:
        """限制分隔条位置，防止某一侧被拖到 0 宽（控件随之消失）。"""
        try:
            total = self.body.winfo_width()
            if total < 100:
                return
            pos = self.body.sashpos(0)
            left_min, right_min = 340, 420
            target = max(left_min, min(pos, total - right_min))
            if abs(target - pos) > 1:
                self.body.sashpos(0, target)
        except Exception:
            pass

    def _apply_dark_title_bar(self) -> None:
        """让系统标题栏跟随深色界面（Win10 1903+，失败就忽略）。"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ 样式
    def _init_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", font=(FONT_UI, 10), background=COLORS["bg"], foreground=COLORS["fg"])
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["fg"])
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["fg2"])
        style.configure("MutedPanel.TLabel", background=COLORS["panel"], foreground=COLORS["fg2"])

        style.configure("TButton", background=COLORS["panel2"], foreground=COLORS["fg"],
                        borderwidth=0, focusthickness=0, padding=(12, 7))
        style.map("TButton",
                  background=[("active", COLORS["border"]), ("disabled", COLORS["panel"])],
                  foreground=[("disabled", COLORS["fg3"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#ffffff",
                        font=(FONT_UI, 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#7aa1ff"), ("disabled", "#2c3a5c")])

        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"],
                        foreground=COLORS["fg"], rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", background=COLORS["panel2"], foreground=COLORS["fg2"],
                        relief="flat", font=(FONT_UI, 9, "bold"))
        style.map("Treeview", background=[("selected", "#243457")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", COLORS["panel2"])])

        style.configure("TEntry", fieldbackground=COLORS["panel2"], foreground=COLORS["fg"],
                        insertcolor=COLORS["fg"], bordercolor=COLORS["border"], padding=6)
        style.map("TEntry",
                  fieldbackground=[("disabled", COLORS["panel"])],
                  foreground=[("disabled", COLORS["fg3"])],
                  bordercolor=[("disabled", COLORS["border"])])
        style.configure("TCombobox", fieldbackground=COLORS["panel2"], background=COLORS["panel2"],
                        foreground=COLORS["fg"], arrowcolor=COLORS["fg2"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel2"])],
                  foreground=[("readonly", COLORS["fg"])])
        self.option_add("*TCombobox*Listbox.background", COLORS["panel2"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])

        style.configure("TSeparator", background=COLORS["border"])
        style.configure("Horizontal.TProgressbar", background=COLORS["accent"],
                        troughcolor=COLORS["panel2"], borderwidth=0)

    # ----------------------------------------------------------------- 菜单
    def _build_menu(self) -> None:
        menubar = tk.Menu(self, tearoff=False)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="添加 ZIP 文件…", command=self.add_files)
        file_menu.add_command(label="添加文件夹…", command=self.add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="清空列表", command=self.clear_all)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        tools = tk.Menu(menubar, tearoff=False)
        tools.add_command(label="生成当前书的 HTML 预览", command=self.open_html_preview)
        tools.add_command(label="打开输出目录", command=self.open_output_dir)
        menubar.add_cascade(label="工具", menu=tools)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(12, 10, 12, 8))
        root.pack(fill="both", expand=True)

        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(0, 10))
        bar.columnconfigure(0, weight=1)

        # 第一行：左侧操作，右侧转换按钮（始终可见）
        actions = ttk.Frame(bar)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Button(actions, text="添加 ZIP", command=self.add_files).pack(side="left")
        ttk.Button(actions, text="添加文件夹", command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="移除所选", command=self.remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="清空", command=self.clear_all).pack(side="left", padx=(6, 0))

        # 第二行：输出目录（输入框自适应，浏览按钮固定可见）
        output_row = ttk.Frame(bar)
        output_row.grid(row=1, column=0, sticky="ew")
        output_row.columnconfigure(1, weight=1)

        ttk.Label(output_row, text="输出目录", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.output_entry = ttk.Entry(output_row, state="disabled")
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=(8, 6))
        ttk.Button(output_row, text="浏览…", command=self.choose_output).grid(row=0, column=2)
        ttk.Checkbutton(output_row, text="与 zip 同目录", variable=self._output_auto,
                        command=self._toggle_output).grid(row=0, column=3, padx=(8, 0))

        # 第三行：并行数 + 快捷键提示 + 转换按钮（单独一行，窄窗口也不会被挤掉）
        convert_row = ttk.Frame(bar)
        convert_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(convert_row, text="并行", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Spinbox(convert_row, from_=1, to=8, width=3, textvariable=self._jobs).pack(side="left")
        ttk.Label(convert_row, text="Ctrl+O 添加 · Ctrl+Enter 开始 · Delete 删除",
                  style="Muted.TLabel").pack(side="left", padx=(14, 0))

        self.convert_button = ttk.Button(convert_row, text="开始转换", style="Accent.TButton",
                                         command=self.start_convert)
        self.convert_button.pack(side="right")
        self.stop_button = ttk.Button(convert_row, text="停止", command=self.stop_convert, state="disabled")
        self.stop_button.pack(side="right", padx=(0, 8))

        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True)
        body.bind("<ButtonRelease-1>", self._on_pane_release, add="+")
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.body = body

        left = ttk.Frame(body)
        body.add(left, weight=3)

        columns = ("title", "pages", "mode", "status")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("title", text="书名（zip 文件名）")
        self.tree.heading("pages", text="页数")
        self.tree.heading("mode", text="类型")
        self.tree.heading("status", text="状态")
        self.tree.column("title", width=300, anchor="w")
        self.tree.column("pages", width=60, anchor="center")
        self.tree.column("mode", width=100, anchor="center")
        self.tree.column("status", width=170, anchor="w")
        self.tree.tag_configure("ok", foreground=COLORS["ok"])
        self.tree.tag_configure("err", foreground=COLORS["err"])
        self.tree.tag_configure("run", foreground=COLORS["accent"])
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        right = ttk.Frame(body, style="Panel.TFrame", padding=(14, 12))
        body.add(right, weight=4)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.empty_hint = ttk.Label(
            right,
            text="左侧还没有书。\n点击「添加 ZIP」或「添加文件夹」开始。",
            style="MutedPanel.TLabel",
            justify="center",
            wraplength=360,
        )
        self.empty_hint.grid(row=0, column=0, sticky="nsew")

        self.editor = ttk.Frame(right, style="Panel.TFrame")
        self.editor.columnconfigure(1, weight=1)
        self.editor.columnconfigure(3, weight=1)

        ttk.Label(self.editor, text="书名", style="MutedPanel.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.title_var = tk.StringVar()
        ttk.Entry(self.editor, textvariable=self.title_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        self.title_var.trace_add("write", lambda *_: self._apply_title())

        ttk.Label(self.editor, text="作者", style="MutedPanel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.author_var = tk.StringVar()
        ttk.Entry(self.editor, textvariable=self.author_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.author_var.trace_add("write", lambda *_: self._apply_author())

        ttk.Label(self.editor, text="类型", style="MutedPanel.TLabel").grid(row=1, column=2, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value=MODE_CHOICES[0])
        self.mode_box = ttk.Combobox(self.editor, textvariable=self.mode_var, values=MODE_CHOICES,
                                     state="readonly", width=16)
        self.mode_box.grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=4)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_mode())

        ttk.Label(self.editor, text="封面", style="MutedPanel.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.cover_var = tk.StringVar()
        self.cover_box = ttk.Combobox(self.editor, textvariable=self.cover_var, state="readonly")
        self.cover_box.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        self.cover_box.bind("<<ComboboxSelected>>", lambda _e: self._apply_cover())

        ttk.Separator(self.editor).grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 10))

        pages_frame = ttk.Frame(self.editor, style="Panel.TFrame")
        pages_frame.grid(row=4, column=0, columnspan=4, sticky="nsew")
        self.editor.rowconfigure(4, weight=2, minsize=150)
        pages_frame.columnconfigure(0, weight=1)

        ttk.Label(pages_frame, text="页面顺序（可上移/下移，双击某页设为封面）",
                  style="MutedPanel.TLabel").grid(row=0, column=0, sticky="w")

        self.page_list = tk.Listbox(
            pages_frame, bg=COLORS["panel2"], fg=COLORS["fg"],
            selectbackground="#2d4a86", selectforeground="#ffffff",
            highlightthickness=0, borderwidth=0, font=(FONT_UI, 10), activestyle="none",
        )
        self.page_list.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        pages_frame.rowconfigure(1, weight=1)
        self.page_list.bind("<<ListboxSelect>>", self.on_page_select)
        self.page_list.bind("<Double-Button-1>", lambda _e: self.set_cover_from_page())

        page_scroll = ttk.Scrollbar(pages_frame, orient="vertical", command=self.page_list.yview)
        self.page_list.configure(yscrollcommand=page_scroll.set)
        page_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 6))

        buttons = ttk.Frame(pages_frame, style="Panel.TFrame")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="上移", width=5, command=lambda: self.move_page(-1)).pack(side="left")
        ttk.Button(buttons, text="下移", width=5, command=lambda: self.move_page(1)).pack(side="left", padx=6)
        ttk.Button(buttons, text="设为封面", width=9, command=self.set_cover_from_page).pack(side="left")
        ttk.Button(buttons, text="HTML 预览", width=9, command=self.open_html_preview).pack(side="right")

        preview = ttk.Frame(self.editor, style="Panel.TFrame")
        preview.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=(12, 0))
        self.editor.rowconfigure(5, weight=3)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)

        nav = ttk.Frame(preview, style="Panel.TFrame")
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(nav, text="预览", style="MutedPanel.TLabel").pack(side="left")
        self.preview_label = ttk.Label(nav, text="", style="MutedPanel.TLabel")
        self.preview_label.pack(side="left", padx=(10, 0))
        ttk.Button(nav, text="▶", width=3,
                   command=lambda: self.show_page(self.preview_index + 1)).pack(side="right")
        ttk.Button(nav, text="◀", width=3,
                   command=lambda: self.show_page(self.preview_index - 1)).pack(side="right", padx=4)

        self.preview_canvas = tk.Label(preview, bg="#0d1016", fg=COLORS["fg2"], bd=0,
                                       text="选择左侧的页码查看预览")
        self.preview_canvas.grid(row=1, column=0, sticky="nsew")
        self.preview_text = tk.Text(preview, bg="#0d1016", fg=COLORS["fg"], bd=0, wrap="word",
                                    font=(FONT_UI, 11), padx=16, pady=12, state="disabled",
                                    takefocus=0)

        log_frame = ttk.Frame(root)
        log_frame.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=5, bg=COLORS["panel"], fg=COLORS["fg2"], bd=0,
                                font=("Consolas", 9), padx=10, pady=8, state="disabled")
        self.log_text.pack(side="left", fill="x", expand=True)

        status = ttk.Frame(root)
        status.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(status, mode="determinate", length=220)
        self.progress.pack(side="right")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")

    # ------------------------------------------------------------- 数据操作
    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择 ZIP 文件", initialdir=self._last_dir,
            filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*.*")],
        )
        if paths:
            self._last_dir = str(Path(paths[0]).parent)
            self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择包含 ZIP 的文件夹", initialdir=self._last_dir)
        if folder:
            self._last_dir = folder
            self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]) -> None:
        zips = collect_zips(paths)
        if not zips:
            self.log("没有找到 zip 文件")
            return

        added = 0
        skipped = 0
        for zip_path in zips:
            key = str(zip_path.resolve())
            if key in self.books:
                continue
            try:
                plan = plan_book(scan_zip(zip_path))
            except Exception as exc:
                self.log(f"[失败] {zip_path.name}: {exc}")
                continue

            if not plan.pages:
                self.log(f"[跳过] {zip_path.name}: 没有可用图片或文字")
                skipped += 1
                continue

            self.books[key] = plan
            self.tree.insert(
                "", "end", iid=key,
                values=(plan.title, len(plan.pages), plan.mode_label(), "待转换"),
            )
            added += 1
            self._last_zip_dir = zip_path.parent

        message = f"已添加 {added} 个 zip（共 {len(self.books)} 本）"
        if skipped:
            message += f"，跳过 {skipped} 个无内容包"
        self.log(message)
        self.status_var.set(f"队列 {len(self.books)} 本")

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            self.books.pop(iid, None)
            self.tree.delete(iid)
        if self.current_key not in self.books:
            self.current_key = None
            self._show_empty()
        self.status_var.set(f"队列 {len(self.books)} 本")

    def clear_all(self) -> None:
        if self._busy:
            messagebox.showinfo("请稍候", "转换进行中，无法清空。")
            return
        self.books.clear()
        self.tree.delete(*self.tree.get_children())
        self.current_key = None
        self._show_empty()
        self.status_var.set("就绪")

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        plan = self.books.get(selection[0])
        if plan is None:
            return
        self.current_key = selection[0]
        self._load_editor(plan)

    def _show_empty(self) -> None:
        self.editor.grid_forget()
        self.empty_hint.grid(row=0, column=0, sticky="nsew")

    def _load_editor(self, plan: BookPlan) -> None:
        self.empty_hint.grid_forget()
        self.editor.grid(row=0, column=0, sticky="nsew")

        self._loading = True
        self.title_var.set(plan.title)
        self.author_var.set(plan.author)
        self.mode_var.set(MODE_LABELS.get(plan.mode, MODE_CHOICES[0]))
        self._refresh_cover_box(plan)
        self._refresh_pages(plan)
        self._loading = False

        self.preview_index = 0
        self.show_page(0)

    def _refresh_cover_box(self, plan: BookPlan) -> None:
        images = plan.image_entries()
        values = ["（无 / 自动）"] + [f"{i + 1}. {entry}" for i, entry in enumerate(images)]
        self._cover_values = [None, *images]
        self.cover_box.configure(values=values)
        if plan.cover_entry and plan.cover_entry in images:
            self.cover_var.set(values[images.index(plan.cover_entry) + 1])
        else:
            self.cover_var.set(values[0])

    def _refresh_pages(self, plan: BookPlan) -> None:
        self.page_list.delete(0, "end")
        for index, page in enumerate(plan.pages, start=1):
            kind = "图" if page.kind == "image" else "文"
            cover = "  · 封面" if plan.cover_entry and page.entry == plan.cover_entry else ""
            self.page_list.insert("end", f"{index:3d}  [{kind}]  {page.display()}{cover}")

    # ------------------------------------------------------------- 编辑回写
    def _current_plan(self) -> BookPlan | None:
        return self.books.get(self.current_key) if self.current_key else None

    def _apply_title(self) -> None:
        plan = self._current_plan()
        if not plan or self._loading:
            return
        title = self.title_var.get().strip()
        if title and title != plan.title:
            plan.title = title
            self._update_row(plan)

    def _apply_author(self) -> None:
        plan = self._current_plan()
        if not plan or self._loading:
            return
        plan.author = self.author_var.get().strip()

    def _apply_mode(self) -> None:
        plan = self._current_plan()
        if not plan or not plan.scan:
            return
        index = MODE_CHOICES.index(self.mode_var.get())
        rebuilt = plan_book(
            plan.scan, mode=MODE_VALUES[index],
            title=plan.title, author=plan.author, cover=plan.cover_entry,
        )
        if not rebuilt.pages:
            messagebox.showwarning("无法切换", "该模式下没有可用页面，已保持原样。")
            self.mode_var.set(MODE_LABELS.get(plan.mode, MODE_CHOICES[0]))
            return

        assert self.current_key is not None
        rebuild_cover = plan.cover_entry if plan.cover_entry in rebuilt.image_entries() else None
        rebuilt.cover_entry = rebuild_cover
        self.books[self.current_key] = rebuilt
        self._load_editor(rebuilt)
        self._update_row(rebuilt)
        self.log(f"「{rebuilt.title}」切换为 {rebuilt.mode_label()}，{len(rebuilt.pages)} 页")

    def _apply_cover(self) -> None:
        plan = self._current_plan()
        if not plan:
            return
        index = self.cover_box.current()
        plan.cover_entry = self._cover_values[index] if 0 <= index < len(self._cover_values) else None
        self._refresh_pages(plan)
        if plan.cover_entry:
            self.log(f"「{plan.title}」封面设为 {plan.cover_entry}")

    def move_page(self, delta: int) -> None:
        plan = self._current_plan()
        if not plan:
            return
        selection = self.page_list.curselection()
        if not selection:
            return
        index = selection[0]
        if not plan.move_page(index, delta):
            return
        self._refresh_pages(plan)
        new_index = index + delta
        self.page_list.selection_set(new_index)
        self.page_list.see(new_index)
        self.show_page(new_index)

    def set_cover_from_page(self) -> None:
        plan = self._current_plan()
        if not plan:
            return
        selection = self.page_list.curselection()
        if not selection:
            return
        page = plan.pages[selection[0]]
        entry = page.entry if page.kind == "image" else (page.images[0] if page.images else None)
        if not entry:
            messagebox.showinfo("无法设为封面", "这一页没有图片。")
            return
        plan.cover_entry = entry
        self._refresh_cover_box(plan)
        self._refresh_pages(plan)
        self.log(f"「{plan.title}」封面设为 {entry}")

    def on_page_select(self, _event=None) -> None:
        selection = self.page_list.curselection()
        if selection:
            self.show_page(selection[0])

    # ------------------------------------------------------------- 预览渲染
    def show_page(self, index: int) -> None:
        plan = self._current_plan()
        if not plan or not plan.pages:
            return
        index = max(0, min(index, len(plan.pages) - 1))
        self.preview_index = index
        page = plan.pages[index]
        self.preview_label.configure(text=f"第 {index + 1} / {len(plan.pages)} 页 · {page.display()}")

        if page.kind == "image":
            self._preview_image(plan, page)
        else:
            self._preview_text_page(plan, page)

    def _show_preview_widget(self, widget: tk.Widget) -> None:
        self.preview_text.grid_forget()
        self.preview_canvas.grid_forget()
        widget.grid(row=1, column=0, sticky="nsew")

    def _preview_image(self, plan: BookPlan, page) -> None:
        try:
            with zipfile.ZipFile(plan.source) as archive:
                raw = archive.read(page.entry)
            image = Image.open(io.BytesIO(raw))
            image.load()
            image.thumbnail((640, 520))
            photo = ImageTk.PhotoImage(image.convert("RGB"))
            self._preview_photo = photo
            self.preview_canvas.configure(image=photo, text="")
        except Exception as exc:
            self._preview_photo = None
            self.preview_canvas.configure(image="", text=f"无法预览：{exc}")
        self._show_preview_widget(self.preview_canvas)

    def _preview_text_page(self, plan: BookPlan, page) -> None:
        try:
            with zipfile.ZipFile(plan.source) as archive:
                text = decode_text(archive.read(page.entry))
            if page.kind == "html":
                text = html_to_text(text)
        except Exception as exc:
            text = f"无法预览：{exc}"

        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", f"{page.heading}\n\n{text.strip()}")
        self.preview_text.configure(state="disabled")
        self._show_preview_widget(self.preview_text)

    def open_html_preview(self) -> None:
        plan = self._current_plan()
        if not plan:
            return
        try:
            folder = data_dir() / "out" / "preview"
            output = render_preview_file(plan, folder / f"{plan.title}.html", max_pages=60)
            webbrowser.open(output.as_uri())
            self.log(f"已生成预览：{output}")
        except Exception as exc:
            messagebox.showerror("预览失败", str(exc))

    # --------------------------------------------------------------- 转换
    def choose_output(self) -> None:
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self._output_auto.set(False)
            self.output_entry.configure(state="normal")
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)
            self.output_entry.configure(state="disabled")

    def _toggle_output(self) -> None:
        self.output_entry.configure(state="disabled")
        if self._output_auto.get():
            self.output_entry.delete(0, "end")

    def _output_dir(self) -> Path | None:
        if self._output_auto.get():
            return None
        value = self.output_entry.get().strip()
        return Path(value) if value else None

    def start_convert(self) -> None:
        if self._busy or not self.books:
            return
        out_dir = self._output_dir()
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)

        # 转换期间用户可以继续编辑，因此给工作线程一份快照
        plans = {plan.source: copy.deepcopy(plan) for plan in self.books.values()}
        zips = list(plans)
        options = ConvertOptions(output_dir=out_dir, jobs=int(self._jobs.get()), overwrite=True)

        self._busy = True
        self._cancel.clear()
        self.convert_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(maximum=len(zips), value=0)
        self.status_var.set(f"正在转换 0 / {len(zips)}")
        for iid in self.books:
            self.tree.set(iid, "status", "排队中")
            self.tree.item(iid, tags=("run",))

        self._worker = threading.Thread(target=self._convert_worker,
                                        args=(zips, options, plans), daemon=True)
        self._worker.start()

    def _convert_worker(self, zips, options, plans) -> None:
        def progress(done: int, total: int, result) -> None:
            self._queue.put(("progress", done, total, result))

        try:
            results = convert_batch(zips, options, progress=progress, cancel=self._cancel, plans=plans)
            self._queue.put(("done", results))
        except Exception as exc:
            self._queue.put(("error", exc))

    def stop_convert(self) -> None:
        self._cancel.set()
        self.status_var.set("正在停止…")

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    self._on_progress(message[1], message[2], message[3])
                elif kind == "done":
                    self._on_done(message[1])
                elif kind == "error":
                    self._on_error(message[1])
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _on_progress(self, done: int, total: int, result) -> None:
        self.progress.configure(maximum=total, value=done)
        self.status_var.set(f"正在转换 {done} / {total}")
        iid = str(result.source.resolve())
        if iid in self.books:
            if result.ok:
                self.tree.set(iid, "status", f"完成 · {result.pages} 页")
                self.tree.item(iid, tags=("ok",))
            else:
                self.tree.set(iid, "status", "失败")
                self.tree.item(iid, tags=("err",))
        if result.ok:
            self.log(f"OK  {result.name} → {result.output.name}（{result.pages} 页，{result.seconds:.1f}s）")
            for warning in result.warnings[:2]:
                self.log(f"    ! {warning}")
        else:
            self.log(f"FAIL {result.name}: {result.error}")

    def _on_done(self, results) -> None:
        self._busy = False
        self.convert_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        ok = sum(1 for r in results if r.ok)
        self.status_var.set(f"完成：成功 {ok}，失败 {len(results) - ok}")
        self.log(f"批次完成：成功 {ok}，失败 {len(results) - ok}")
        if ok and messagebox.askyesno(
            "转换完成", f"成功 {ok} 本，失败 {len(results) - ok} 本。\n是否打开输出目录？"
        ):
            self.open_output_dir()

    def _on_error(self, exc: Exception) -> None:
        self._busy = False
        self.convert_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set("转换失败")
        self.log(f"批次异常：{exc}")
        messagebox.showerror("转换失败", str(exc))

    # --------------------------------------------------------------- 杂项
    def _update_row(self, plan: BookPlan) -> None:
        if self.current_key and self.current_key in self.books:
            self.tree.set(self.current_key, "title", plan.title)
            self.tree.set(self.current_key, "pages", len(plan.pages))
            self.tree.set(self.current_key, "mode", plan.mode_label())

    def open_output_dir(self) -> None:
        folder = self._output_dir() or self._last_zip_dir or Path.home()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror("打开目录失败", str(exc))

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于 zip2epub",
            "zip2epub 1.0\n\n"
            "批量把 zip 内的图片按顺序转成 EPUB 3。\n"
            "· 书名取 zip 文件名，封面取首张图片\n"
            "· 支持纯文字 / 图片 / 图文混排\n"
            "· 全程本地运行：无联网、无内容审查\n",
        )

    def on_close(self) -> None:
        if self._busy and not messagebox.askyesno("转换进行中", "仍在转换，确定退出吗？"):
            return
        self.destroy()


def main() -> int:
    app = Zip2EpubApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
