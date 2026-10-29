"""无交互冒烟测试：确保 GUI 能构建、加载样本、渲染预览。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.app import Zip2EpubApp  # noqa: E402


def main() -> int:
    app = Zip2EpubApp()
    app.update_idletasks()

    app._add_paths([ROOT / "sample_data"])
    app.update()

    children = app.tree.get_children()
    assert children, "没有加载到任何 zip"
    print(f"loaded {len(children)} books")

    mixed_iid = None
    for iid in children:
        plan = app.books[iid]
        if plan.effective_mode() == "mixed":
            mixed_iid = iid
            break

    # 图片模式：加载编辑器 + 预览
    app.tree.selection_set(children[0])
    app.on_select()
    app.update()
    app.show_page(0)
    app.update()
    print("image preview ok")

    # 图文混排：切换模式按钮需要事件循环，这里直接调用内部逻辑
    if mixed_iid:
        app.tree.selection_set(mixed_iid)
        app.on_select()
        app.update()
        app.show_page(0)  # html 章节 → 文本框预览
        app.update()
        print("html preview ok")

        app.move_page(0) if app.page_list.size() > 1 else None
        app.page_list.selection_set(0)
        app.set_cover_from_page()
        app.update()
        print("cover set ok")

    app.destroy()
    print("GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
