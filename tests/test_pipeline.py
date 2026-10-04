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
        """Kullanıcının klasörüne geçici bir şey yazılmamalı (çalışma klasörü sistemin geçici klasöründe)."""
        return [n for n in os.listdir(self.dir) if "metaclean-" in n]

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

    def test_save_copy_clean_with_fresh_timestamps(self):
        path = self.copy("foto.jpg")
        os.utime(path, (1_000_000_000, 1_000_000_000))  # 2001
        sig = signature(path)
        job = Job(path)
        self.assertTrue(job.prepare().ok)
        self.assertEqual(self.leftovers(), [], "onaydan önce kullanıcının klasörüne yazılmamalı")
        before = time.time()
        dest = os.path.join(self.dir, "kopya.jpg")
        notes = job.save_copy(dest)
        st = os.stat(dest)
        self.assertGreaterEqual(st.st_mtime, before - 2, "orijinalin 2001 tarihi kopyaya taşınmamalı")
        self.assertFalse(any(n.startswith("✗") for n in notes), notes)
        if hasattr(st, "st_birthtime"):
            self.assertGreaterEqual(st.st_birthtime, before - 2)
        with open(dest, "rb") as f:
            data = f.read()
        self.assertNotIn(b"gizli yorum", data)
        self.assertNotIn(b"ftypmp42", data)
        self.assertEqual(signature(path), sig, "orijinal değişmemeli")
        self.assertEqual(self.leftovers(), [])

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
        sig = signature(real)
        job = Job(link)
        self.assertTrue(job.prepare().ok)
        dest = os.path.join(self.dir, "kopya.jpg")
        job.save_copy(dest)
        self.assertTrue(os.path.islink(link), "bağın kendisi değiştirilmemeli")
        self.assertEqual(signature(real), sig, "bağın gösterdiği orijinal değişmemeli")
        with open(dest, "rb") as f:
            self.assertNotIn(b"gizli yorum", f.read())

    def test_unsupported_rejected(self):
        path = os.path.join(self.dir, "metin.jpg")  # uzantı yalan söylüyor
        with open(path, "w", encoding="utf-8") as f:
            f.write("bu bir resim değil")
        r = Job(path).prepare()
        self.assertIn("Desteklenmeyen", r.error or "")
        self.assertEqual(self.leftovers(), [])

    def test_newline_in_filename_cannot_inject_exiftool_args(self):
        # ExifTool -@ arg dosyası satır başına bir argüman alır; dosya adındaki satır sonu
        # -if/-api ile Perl komutu enjekte etmeye çalışabilir. Bu ad ExifTool'a hiç ulaşmamalı.
        # (Dosyayı gerçekten oluşturmuyoruz: Windows satır sonlu ad oluşturmaya zaten izin vermez;
        #  savunma, adın ExifTool arg dosyasına yazıldığı katmanda sınanıyor.)
        evil = "a.jpg\n-if\nsystem(q{touch KANIT}) || 1\na.jpg"
        with self.assertRaises(tools.ToolError) as cm:
            tools.exiftool_scan(os.path.join(self.dir, evil))
        self.assertIn("satır sonu", str(cm.exception))
        with self.assertRaises(tools.ToolError):
            tools.exiftool_write(["-all=", "-o", os.path.join(self.dir, evil)])

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

    def test_ultrahdr_gain_map_preserved(self):
        import subprocess

        from PIL import Image
        path = self.copy("hdr.jpg")
        job = Job(path)
        r = job.prepare()
        self.assertTrue(r.ok, [(g.name, g.details) for g in r.gates if not g.passed])
        with open(job.out, "rb") as f:
            clean = f.read()
        self.assertIn(b"hdr-gain-map", clean, "Ultra HDR işareti korunmalı")
        for leaked in (b"Istanbul", b"Ayse Gizli", b"derinlik", b"Canon"):
            self.assertNotIn(leaked, clean)

        def exif(file, *args):
            return subprocess.run(["exiftool", "-s3", *args, file], capture_output=True, check=True).stdout

        self.assertEqual(exif(job.out, "-MPF:NumberOfImages").strip(), b"2", "derinlik haritası silinmeli")
        # Kazanç haritasını ExifTool ile (uygulamadan bağımsız) çıkar, pikselleri karşılaştır
        import io
        orig_gain = Image.open(io.BytesIO(exif(path, "-b", "-MPImage2")))
        clean_gain = Image.open(io.BytesIO(exif(job.out, "-b", "-MPImage2")))
        self.assertEqual(orig_gain.tobytes(), clean_gain.tobytes())
        job.discard()

    def test_equivalence_gate_catches_lost_gain_map(self):
        orig_clean = images.JpegHandler.clean

        def drop_gain_map(self_, ctx):
            out = orig_clean(self_, ctx)
            with open(out, "rb") as f:
                data = f.read()
            items, _ = images.parse_jpeg(data)
            with open(out, "wb") as f:
                f.write(images.build_jpeg([(m, p) for m, p in items if not images._is_mpf(m, p)]))
            return out

        with mock.patch.object(images.JpegHandler, "clean", drop_gain_map):
            r = Job(self.copy("hdr.jpg")).prepare()
        eq = next(g for g in r.gates if g.name == "Eşdeğerlik")
        self.assertFalse(eq.passed)
        self.assertTrue(any("kazanç haritası" in d for d in eq.details), eq.details)

    def test_cancel(self):
        job = Job(self.copy("video.mp4"))
        job.cancel.set()
        r = job.prepare()
        self.assertEqual(r.error, "İptal edildi.")
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
