"""Harici araç gerektirmeyen birim testleri: sınıflandırma, kayıt gizliliği, dosya işlemleri."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metaclean  # noqa: E402
from metaclean import applog  # noqa: E402
from metaclean.core import categories, fsops  # noqa: E402
from metaclean.core.report import Field, Report  # noqa: E402
from metaclean.core.session import in_managed_library  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_semver(self):
        self.assertRegex(metaclean.__version__, r"^\d+\.\d+\.\d+$")

    def test_spec_reads_single_source(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "packaging", "metaclean.spec"), encoding="utf-8") as f:
            spec = f.read()
        self.assertNotRegex(spec, r'"CFBundleShortVersionString":\s*"\d')  # elle yazılmış sürüm yok


class CategoryTests(unittest.TestCase):
    def summarize(self, fields, removed=(), scan=None):
        r = Report(path="x")
        r.found = [Field(g, n, v) for g, n, v in fields]
        r.removed = list(removed)
        r.scan = scan or {}
        return {title: value for _, title, value in categories.summarize(r)}

    def test_basic_categories(self):
        out = self.summarize([("GPS", "GPSLatitude", "41 deg 0' 29.50\" N"), ("IFD0", "Make", "Canon"),
                              ("IFD0", "Artist", "Ayşe"), ("ExifIFD", "DateTimeOriginal", "2026:08:15 14:22:33")])
        self.assertIn("Konum", out)
        self.assertEqual(out["Cihaz bilgisi"], "Canon")
        self.assertEqual(out["İsim / sahip"], "Ayşe")
        self.assertIn("Tarih ve saat", out)

    def test_gps_position_formatted(self):
        out = self.summarize([("GPS", "GPSLatitude", "x")], scan={"Composite:GPSPosition": "41 deg 0' 29.50\" N, 28 deg 58' 42.00\" E"})
        self.assertEqual(out["Konum"], "41°0'29.50\" N, 28°58'42.00\" E")

    def test_generic_exif_text_is_not_thumbnail(self):
        # Gerileme: EXIF bloğunun açıklamasında "küçük resim" geçtiği için yanlış etiketleniyordu
        out = self.summarize([("XMP-dc", "Subject", "Photo Booth")],
                             removed=["EXIF (APP1) – kamera, tarih, GPS, küçük resim, üretici notları, 62 bayt"])
        self.assertNotIn("Küçük resim / kapak", out)

    def test_real_thumbnail_and_cover(self):
        self.assertIn("Küçük resim / kapak", self.summarize([], removed=["JFIF küçük resmi (300 bayt)"]))
        self.assertIn("Küçük resim / kapak", self.summarize([], removed=["İz 1: kapak resmi (mjpeg)"]))

    def test_creator_tool_is_history_not_person(self):
        self.assertEqual(categories.categorize("XMP-xmp", "CreatorTool"), "gecmis")

    def test_numeric_values_hidden(self):
        out = self.summarize([("XMP-dc", "Rating", "5")])
        self.assertEqual(out["Açıklama ve etiketler"], "")


class LogPrivacyTests(unittest.TestCase):
    CASES = [
        "add_paths: ['/Users/ayse/Documents/Fotoğraf - 2.10.2026 21.35.jpg']",
        "bitti: /private/var/folders/x/IMG_2465_Original.jpeg -> /Users/a/Desktop/Paylaşıma Hazır/foto-1.jpg hata=None",
        "add_paths: ['C:\\\\Users\\\\Ayşe\\\\Desktop\\\\tatil video.mp4']",
        "cwd /home/ayse/projeler",
    ]

    def test_no_names_or_paths_survive(self):
        for text in self.CASES:
            out = applog.redact(text)
            for leaked in ("ayse", "Ayşe", "Fotoğraf", "IMG_2465", "tatil", "Users", "home"):
                self.assertNotIn(leaked, out, out)

    def test_extension_kept_for_debugging(self):
        self.assertIn("<dosya>.jpg", applog.redact("x /tmp/a b/c.jpg"))

    def test_setup_writes_redacted(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(applog, "log_dir", lambda: tmp):
            path = applog.setup()
            import logging
            logging.getLogger("metaclean.test").info("dosya: %s", "/Users/ayse/gizli.jpg")
            for h in logging.getLogger("metaclean").handlers:
                h.flush()
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("<dosya>.jpg", text)
            self.assertNotIn("ayse", text)
            for h in logging.getLogger("metaclean").handlers:
                h.close()


class FsOpsTests(unittest.TestCase):
    def test_retry_until_unlocked(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("kilitli")

        fsops.retry_on_lock(flaky, "Deneme", delay=0)
        self.assertEqual(calls["n"], 3)

    def test_gives_clear_error(self):
        def locked():
            raise PermissionError("kilitli")

        with self.assertRaisesRegex(PermissionError, "başka bir program"):
            fsops.retry_on_lock(locked, "Deneme", attempts=3, delay=0)

    def test_managed_library(self):
        self.assertTrue(in_managed_library("/Users/a/Pictures/Photos Library.photoslibrary/originals/1/x.jpg"))
        self.assertFalse(in_managed_library("/Users/a/Pictures/foto.jpg"))


if __name__ == "__main__":
    unittest.main()
