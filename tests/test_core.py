"""核心逻辑测试：扫描、排序、计划、EPUB 结构、批量转换。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from z2e.convert import ConvertOptions, collect_zips, convert_batch  # noqa: E402
from z2e.epub import EpubBuilder, validate_epub  # noqa: E402
from z2e.model import MODE_IMAGES, MODE_TEXT  # noqa: E402
from z2e.scan import decode_text, natural_key, plan_book, scan_zip  # noqa: E402

SAMPLE = ROOT / "sample_data"


def ensure_samples() -> None:
    if not (SAMPLE / "漫画示例.zip").exists():
        sys.path.insert(0, str(ROOT / "tools"))
        import make_samples

        make_samples.main()


class NaturalSortTest(unittest.TestCase):
    def test_numeric_order(self) -> None:
        names = ["10.png", "2.png", "1.png", "01.png"]
        ordered = sorted(names, key=natural_key)
        self.assertEqual(ordered, ["1.png", "01.png", "2.png", "10.png"])

    def test_chinese_and_dirs(self) -> None:
        names = ["第2话/01.png", "第10话/01.png", "第1话/02.png", "第1话/01.png"]
        ordered = sorted(names, key=natural_key)
        self.assertEqual(
            ordered, ["第1话/01.png", "第1话/02.png", "第2话/01.png", "第10话/01.png"]
        )


class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_samples()

    def test_comic(self) -> None:
        scan = scan_zip(SAMPLE / "漫画示例.zip")
        self.assertEqual(scan.title, "漫画示例")
        self.assertEqual(scan.auto_mode, "images")
        self.assertEqual(len(scan.entries), 10)
        self.assertEqual([e.name for e in scan.entries][:2], ["第1话/01.png", "第1话/02.png"])

    def test_text(self) -> None:
        scan = scan_zip(SAMPLE / "纯文字示例.zip")
        self.assertEqual(scan.auto_mode, "text")
        self.assertEqual(len(scan.images), 0)

    def test_mixed(self) -> None:
        scan = scan_zip(SAMPLE / "图文混排示例.zip")
        self.assertEqual(scan.auto_mode, "mixed")

    def test_junk_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "junk.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("__MACOSX/._01.png", b"x")
                archive.writestr("Thumbs.db", b"x")
                archive.writestr(".DS_Store", b"x")
                archive.writestr("ok/01.png", b"x")
            scan = scan_zip(path)
            self.assertEqual([e.name for e in scan.entries], ["ok/01.png"])

    def test_broken_zip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.zip"
            path.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                scan_zip(path)


class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_samples()

    def test_images_mode(self) -> None:
        plan = plan_book(scan_zip(SAMPLE / "漫画示例.zip"))
        self.assertEqual(plan.effective_mode(), MODE_IMAGES)
        self.assertEqual(len(plan.pages), 10)
        self.assertEqual(plan.pages[0].kind, "image")
        self.assertEqual(plan.default_cover(), "第1话/01.png")

    def test_mixed_mode_excludes_inline_images(self) -> None:
        plan = plan_book(scan_zip(SAMPLE / "图文混排示例.zip"))
        self.assertEqual([p.kind for p in plan.pages], ["html", "html", "image", "text"])
        self.assertIn("images/scene1.png", plan.pages[0].images)
        self.assertNotIn("images/scene1.png", [p.entry for p in plan.pages])
        self.assertEqual(plan.pages[2].entry, "images/extra.png")

    def test_text_mode(self) -> None:
        plan = plan_book(scan_zip(SAMPLE / "纯文字示例.zip"))
        self.assertEqual(plan.effective_mode(), MODE_TEXT)
        self.assertTrue(all(p.kind == "text" for p in plan.pages))
        self.assertIsNone(plan.default_cover())

    def test_move_page(self) -> None:
        plan = plan_book(scan_zip(SAMPLE / "漫画示例.zip"))
        first = plan.pages[0].entry
        self.assertTrue(plan.move_page(0, 1))
        self.assertEqual(plan.pages[1].entry, first)
        self.assertFalse(plan.move_page(0, -1))

    def test_gbk_decode(self) -> None:
        raw = "中文编码测试".encode("gb18030")
        self.assertEqual(decode_text(raw), "中文编码测试")


class EpubBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_samples()
        cls.temp = Path(tempfile.mkdtemp(prefix="z2e-test-"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def _build(self, name: str, *args) -> Path:
        plan = plan_book(scan_zip(SAMPLE / name), *args)
        output = self.temp / (name.replace(".zip", "") + ".epub")
        EpubBuilder(plan, output).build()
        return output

    def test_comic_epub(self) -> None:
        output = self._build("漫画示例.zip")
        self.assertEqual(validate_epub(output), [])

        with zipfile.ZipFile(output) as archive:
            infos = archive.infolist()
            self.assertEqual(infos[0].filename, "mimetype")
            self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")

            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn("<dc:title>漫画示例</dc:title>", opf)
            self.assertIn('properties="cover-image"', opf)
            self.assertEqual(opf.count("<itemref "), 11)  # 封面 + 10 页

            page = archive.read("OEBPS/pages/p0001.xhtml").decode("utf-8")
            self.assertIn('src="../images/img_0001.png"', page)

    def test_text_epub_has_generated_cover(self) -> None:
        output = self._build("纯文字示例.zip")
        self.assertEqual(validate_epub(output), [])
        with zipfile.ZipFile(output) as archive:
            self.assertIn("OEBPS/images/cover.png", archive.namelist())
            cover = archive.read("OEBPS/cover.xhtml").decode("utf-8")
            self.assertIn('src="images/cover.png"', cover)
            chapter = archive.read("OEBPS/pages/p0001.xhtml").decode("utf-8")
            self.assertIn("第一章 开端", chapter)

    def test_mixed_epub_rewrites_images(self) -> None:
        output = self._build("图文混排示例.zip")
        self.assertEqual(validate_epub(output), [])
        with zipfile.ZipFile(output) as archive:
            chapter = archive.read("OEBPS/pages/p0001.xhtml").decode("utf-8")
            self.assertIn("../images/", chapter)
            self.assertNotIn("<script", chapter)
            self.assertIn("雨落在青石板上", chapter)

    def test_webp_converted(self) -> None:
        output = self._build("webp示例.zip")
        with zipfile.ZipFile(output) as archive:
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn('media-type="image/jpeg"', opf)
            names = archive.namelist()
            self.assertTrue(any(name.endswith(".jpg") for name in names))


class BatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_samples()

    def test_batch_converts_all(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out"
            zips = collect_zips([SAMPLE])
            self.assertGreaterEqual(len(zips), 4)

            results = convert_batch(zips, ConvertOptions(output_dir=output, jobs=4))
            ok = [r for r in results if r.ok]
            self.assertEqual(len(ok), 4)  # 4 个可用样本，另一个故意无内容
            for result in ok:
                self.assertTrue(result.output.exists())
                self.assertEqual(validate_epub(result.output), [])

    def test_duplicate_titles_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("a", "b"):
                sub = root / name
                sub.mkdir()
                with zipfile.ZipFile(sub / "同名.zip", "w") as archive:
                    archive.writestr("01.png", b"\x89PNG\r\n\x1a\n")

            zips = collect_zips([root])
            self.assertEqual(len(zips), 2)
            results = convert_batch(zips, ConvertOptions(output_dir=root / "out", jobs=2))
            names = {r.output.name for r in results if r.ok}
            self.assertEqual(len(names), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
