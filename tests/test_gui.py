"""Arayüz testleri (ekransız): ana ekran dosyayı işler, temiz kopyayı kaydeder, sonucu doğru gösterir."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metaclean.core import tools  # noqa: E402

HAVE_TOOLS = all(tools.find_tool(t) for t in tools.TOOLS)
try:
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


def make_photo(path: str) -> None:
    from PIL import Image
    im = Image.new("RGB", (120, 80), (30, 140, 90))
    ex = im.getexif()
    ex[0x010F], ex[0x0110], ex[0x013B] = "Canon", "EOS Test", "Ayşe"
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (41.0, 0.0, 29.5), "E", (28.0, 58.0, 42.0)
    im.save(path, exif=ex.tobytes())


@unittest.skipUnless(HAVE_QT and HAVE_TOOLS, "PySide6 ve exiftool/ffmpeg/ffprobe gerekli")
class SimpleWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="metaclean-gui-")
        self.out = os.path.join(self.tmp, "cikti")
        from metaclean.gui import simple_window
        self.sw = simple_window
        self.patch = mock.patch.object(simple_window, "output_dir", lambda: self.out)
        self.patch.start()
        self.win = simple_window.SimpleWindow()
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait(self, timeout=60):
        t = time.time()
        while any(not c.done for c in self.win.cards.values()) and time.time() - t < timeout:
            self.app.processEvents()
            time.sleep(0.02)
        for _ in range(5):
            self.app.processEvents()

    def card_text(self, card) -> str:
        return " ".join(l.text() for l in card.findChildren(QLabel))

    def test_photo_cleaned_and_summarized(self):
        src = os.path.join(self.tmp, "IMG_20260815_142233.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertTrue(card.done)
        self.assertIsNotNone(card.dest, card.status.text())
        self.assertTrue(os.path.isfile(card.dest))
        self.assertEqual(os.path.basename(card.dest), "foto-1.jpg", "tarih taşıyan ad paylaşılmamalı")
        text = self.card_text(card)
        for title in ("Paylaşıma hazır", "Konum", "Cihaz bilgisi", "İsim / sahip"):
            self.assertIn(title, text)
        self.assertTrue(card.btn_show.isVisibleTo(self.win))
        with open(card.dest, "rb") as f:
            data = f.read()
        self.assertNotIn(b"Canon", data)

    def test_unsupported_file_explained(self):
        src = os.path.join(self.tmp, "belge.jpg")
        with open(src, "w", encoding="utf-8") as f:
            f.write("bu bir resim değil")
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertIsNone(card.dest)
        self.assertIn("desteklenmiyor", card.status.text())
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_photos_library_refused_with_explanation(self):
        with mock.patch.object(QMessageBox, "information") as info:
            self.win.add_paths(["/Users/x/Pictures/Photos Library.photoslibrary/resources/a.jpeg"])
        info.assert_called_once()
        self.assertEqual(self.win.cards, {})

    def test_parallel_jobs_get_unique_names(self):
        paths = []
        for i in range(4):
            p = os.path.join(self.tmp, f"p{i}.jpg")
            make_photo(p)
            paths.append(p)
        self.win.add_paths(paths)
        self.wait()
        names = sorted(os.path.basename(c.dest) for c in self.win.cards.values())
        self.assertEqual(names, ["foto-1.jpg", "foto-2.jpg", "foto-3.jpg", "foto-4.jpg"])

    def test_advanced_window_opens(self):
        self.win.open_advanced()
        self.assertTrue(self.win.advanced.isVisible())
        self.assertIn("Gelişmiş", self.win.advanced.windowTitle())


if __name__ == "__main__":
    unittest.main()
