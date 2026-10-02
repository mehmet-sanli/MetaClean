"""Uçtan uca testler. Çalıştırma: python -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metaclean.core import tools  # noqa: E402
from metaclean.core.handlers import images, video  # noqa: E402
from metaclean.core.session import Job, signature  # noqa: E402

HAVE_TOOLS = all(tools.find_tool(t) for t in tools.TOOLS)


@unittest.skipUnless(HAVE_TOOLS, "exiftool/ffmpeg/ffprobe gerekli")
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = tempfile.mkdtemp(prefix="metaclean-ornek-")
        import make_samples
        make_samples.main(cls.samples)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.samples, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="metaclean-test-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def copy(self, name: str) -> str:
        dst = os.path.join(self.dir, name)
        shutil.copyfile(os.path.join(self.samples, name), dst)
        return dst

    def leftovers(self):
        return [n for n in os.listdir(self.dir) if n.startswith(".metaclean-")]

    # ---------------------------------------------------------------- olumlu
    def test_all_formats_pass_all_gates(self):
        for name in sorted(os.listdir(self.samples)):
            with self.subTest(name=name):
                job = Job(self.copy(name))
                r = job.prepare()
                self.assertIsNone(r.error, r.error)
                self.assertTrue(r.ok, [(g.name, g.details) for g in r.gates if not g.passed])
                self.assertTrue(r.found, "orijinalde hassas alan bulunmalıydı")
                self.assertEqual(r.remaining, [])
                job.discard()
        self.assertEqual(self.leftovers(), [])

    def test_save_replace_atomic_and_timestamps(self):
        path = self.copy("foto.jpg")
        os.chmod(path, 0o640)
        os.utime(path, (1_000_000_000, 1_000_000_000))  # 2001
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        before = time.time()
        notes = job.save_replace()
        st = os.stat(path)
        self.assertGreaterEqual(st.st_mtime, before - 2)
        if os.name != "nt":  # Windows'ta chmod yalnızca salt-okunur bayrağını değiştirir
            self.assertEqual(st.st_mode & 0o777, 0o640, "izinler korunmalı")
        self.assertFalse(any(n.startswith("✗") for n in notes), notes)
        if hasattr(st, "st_birthtime"):
            self.assertGreaterEqual(st.st_birthtime, before - 2)
        with open(path, "rb") as f:
            data = f.read()
        self.assertNotIn(b"gizli yorum", data)
        self.assertNotIn(b"ftypmp42", data)
        self.assertEqual(self.leftovers(), [])

    def test_fixed_timestamp(self):
        path = self.copy("ses.flac")
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        when = 1_767_225_600  # 2026-01-01
        job.save_replace(when)
        self.assertAlmostEqual(os.stat(path).st_mtime, when, delta=2)

    def test_save_copy_leaves_original_untouched(self):
        path = self.copy("ekran.png")
        sig = signature(path)
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        dest = os.path.join(self.dir, "kopya.png")
        job.save_copy(dest)
        self.assertEqual(signature(path), sig)
        self.assertTrue(os.path.exists(dest))
        self.assertEqual(self.leftovers(), [])

    def test_copy_onto_original_refused(self):
        path = self.copy("ekran.png")
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        with self.assertRaises(RuntimeError):
            job.save_copy(path)

    @unittest.skipIf(os.name == "nt", "sembolik bağ için yönetici izni gerekebilir")
    def test_symlink_resolves_to_real_file(self):
        real = self.copy("foto.jpg")
        link = os.path.join(self.dir, "bag.jpg")
        os.symlink(real, link)
        job = Job(link)
        self.assertTrue(job.prepare().ok)
        job.save_replace()
        self.assertTrue(os.path.islink(link), "bağın kendisi değiştirilmemeli")
        with open(real, "rb") as f:
            self.assertNotIn(b"gizli yorum", f.read())

    def test_original_changed_before_save(self):
        path = self.copy("foto.jpg")
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        with open(path, "ab") as f:
            f.write(b"x")
        with self.assertRaises(RuntimeError):
            job.save_replace()
        self.assertEqual(self.leftovers(), [])

    def test_unsupported_rejected(self):
        path = os.path.join(self.dir, "metin.jpg")  # uzantı yalan söylüyor
        with open(path, "w") as f:
            f.write("bu bir resim değil")
        r = Job(path).prepare()
        self.assertIn("Desteklenmeyen", r.error or "")
        self.assertEqual(self.leftovers(), [])

    # ---------------------------------------------------------------- kapılar gerçekten yakalıyor mu
    def test_equivalence_gate_catches_pixel_change(self):
        orig_clean = images.JpegHandler.clean

        def corrupt(self_, ctx):
            out = orig_clean(self_, ctx)
            with open(out, "rb") as f:
                data = bytearray(f.read())
            data[-200] ^= 0x01  # tarama verisinde tek bit
            with open(out, "wb") as f:
                f.write(data)
            return out

        with mock.patch.object(images.JpegHandler, "clean", corrupt):
            r = Job(self.copy("foto.jpg")).prepare()
        self.assertFalse(r.ok)
        self.assertEqual(self.leftovers(), [], "başarısızlıkta temp silinmeli")

    def test_cleanliness_gate_catches_leftover_comment(self):
        orig_clean = images.JpegHandler.clean

        def leaky(self_, ctx):
            out = orig_clean(self_, ctx)
            with open(out, "rb") as f:
                data = f.read()
            com = b"\xff\xfe" + (len(b"sizinti") + 2).to_bytes(2, "big") + b"sizinti"
            with open(out, "wb") as f:
                f.write(data[:2] + com + data[2:])
            return out

        with mock.patch.object(images.JpegHandler, "clean", leaky):
            r = Job(self.copy("foto.jpg")).prepare()
        cleanliness = next(g for g in r.gates if g.name == "Temizlik")
        self.assertFalse(cleanliness.passed)
        self.assertTrue(any("Comment" in f.key for f in r.remaining))

    def test_equivalence_gate_catches_lost_rotation(self):
        real_run = tools.run

        def no_rotation(args, **kw):
            if "-c" in args and "copy" in args and "-map_metadata" in args:
                i = args.index("-i")
                args = args[:i] + ["-display_rotation", "0"] + args[i:]
            return real_run(args, **kw)

        with mock.patch.object(video.tools, "run", no_rotation):
            r = Job(self.copy("video.mp4")).prepare()
        eq = next(g for g in r.gates if g.name == "Eşdeğerlik")
        self.assertFalse(eq.passed)
        self.assertTrue(any("yan veri" in d for d in eq.details), eq.details)

    def test_cancel(self):
        job = Job(self.copy("video.mp4"))
        job.cancel.set()
        r = job.prepare()
        self.assertEqual(r.error, "İptal edildi.")
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
