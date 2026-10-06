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
        # Kapatırken "kaydedilmemiş dosyalar" sorusu testi bekletmesin
        self.ask = mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
        self.ask.start()
        self.win = simple_window.SimpleWindow()
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.ask.stop()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait(self, timeout=60):
        t = time.time()
        while any(not c.done or c.saving for c in self.win.cards.values()) and time.time() - t < timeout:
            self.app.processEvents()
            time.sleep(0.02)
        for _ in range(5):
            self.app.processEvents()

    def save(self, card):
        """Kullanıcının "Kaydet"e basması."""
        self.assertTrue(card.btn_save.isVisibleTo(self.win), card.status.text())
        card.btn_save.click()
        self.wait()
        return card.dest

    def card_text(self, card) -> str:
        return " ".join(l.text() for l in card.findChildren(QLabel))

    def test_photo_cleaned_and_summarized(self):
        src = os.path.join(self.tmp, "IMG_20260815_142233.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertTrue(card.done)
        self.assertIsNone(card.dest, "Kaydet'e basılmadan kaydedilmemeli")
        self.save(card)
        self.assertIsNotNone(card.dest, card.status.text())
        self.assertTrue(os.path.isfile(card.dest))
        self.assertEqual(os.path.basename(card.dest), "foto-1.jpg", "tarih taşıyan ad paylaşılmamalı")
        text = self.card_text(card)
        for title in ("Kaydedildi", "Konum", "Cihaz bilgisi", "İsim / sahip"):
            self.assertIn(title, text)
        self.assertTrue(card.btn_show.isVisibleTo(self.win))
        with open(card.dest, "rb") as f:
            data = f.read()
        self.assertNotIn(b"Canon", data)

    def test_metadata_panel_shows_readable_fields(self):
        src = os.path.join(self.tmp, "a.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.assertTrue(self.win.meta.isVisibleTo(self.win), "panel dosya bırakılınca hemen açılmalı")
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertIs(self.win.meta_card, card)
        text = self.win.meta.body.toPlainText()
        for expected in ("Konum (koordinat)", "Cihaz markası", "Canon", "Kaydedince silinecek"):
            self.assertIn(expected, text)
        self.assertNotIn("GPSLatitude", text, "teknik alan adları gösterilmemeli")
        self.assertIn("Kaydet", self.win.meta.subtitle.text())
        self.save(card)
        self.assertIn("Silindi", self.win.meta.body.toPlainText())
        self.assertIn("hepsi silindi", self.win.meta.subtitle.text())

    def test_metadata_panel_follows_clicked_card(self):
        a, b = os.path.join(self.tmp, "a.jpg"), os.path.join(self.tmp, "b.jpg")
        make_photo(a)
        make_photo(b)
        self.win.add_paths([a, b])
        self.wait()
        first = [c for c in self.win.cards.values() if c.job.path == a][0]
        self.win.show_meta(first)
        self.assertIn("a.jpg", self.win.meta.title.text())
        self.assertEqual(first.property("selected"), "true")

    def _clean_one(self, src):
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.save(card)
        self.assertIsNotNone(card.dest, card.status.text())
        return card.dest

    def test_original_dates_not_carried_to_copy(self):
        src = os.path.join(self.tmp, "eski.jpg")
        make_photo(src)
        old = time.mktime((2019, 5, 10, 12, 30, 0, 0, 0, -1))
        os.utime(src, (old, old))
        st = os.stat(self._clean_one(src))
        self.assertGreater(st.st_mtime, old + 86400, "orijinalin tarihi kopyaya sızmamalı")

    def test_gallery_file_cleaned_and_temp_original_removed(self):
        # macOS galerisi (dosya sözü) dosyayı geçici klasöre yazar; temizlikten sonra o kopya silinmeli
        gallery = tempfile.mkdtemp(prefix="metaclean-galeri-", dir=self.tmp)
        src = os.path.join(gallery, "IMG_1234.jpg")
        make_photo(src)
        self.win.signals.received.emit(src)
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertTrue(card.from_gallery)
        self.assertIn("galerideki fotoğrafa dokunulmadı", card.note.text())
        self.save(card)
        self.assertIsNotNone(card.dest, card.status.text())
        self.assertTrue(os.path.isfile(card.dest))
        self.assertFalse(os.path.exists(src), "konum taşıyan geçici orijinal diskte kalmamalı")
        self.assertFalse(os.path.exists(gallery), "boşalan geçici galeri klasörü de kalmamalı")
        self.assertIn("Konum (koordinat)", self.win.meta.body.toPlainText())

    def test_nothing_written_until_save_pressed(self):
        folder = os.path.join(self.tmp, "kullanici")
        os.makedirs(folder)
        src = os.path.join(folder, "a.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertTrue(card.waiting)
        self.assertFalse(os.path.exists(self.out), "onaysız Paylaşıma Hazır klasörü bile açılmamalı")
        self.assertEqual(os.listdir(folder), ["a.jpg"], "kullanıcının klasörüne geçici dosya yazılmamalı")
        self.assertIn("henüz kaydedilmedi", card.status.text())
        self.save(card)
        self.assertEqual(os.listdir(self.out), ["foto-1.jpg"])

    def test_discard_saves_nothing(self):
        src = os.path.join(self.tmp, "a.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        card.btn_discard.click()
        self.assertTrue(card.discarded)
        self.assertIsNone(card.dest)
        self.assertFalse(card.btn_save.isVisibleTo(self.win))
        self.assertFalse(os.path.exists(self.out))
        self.assertIn("Vazgeçildi", self.win.meta.subtitle.text())

    def test_close_asks_when_unsaved(self):
        src = os.path.join(self.tmp, "a.jpg")
        make_photo(src)
        self.win.add_paths([src])
        self.wait()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No) as q:
            self.assertFalse(self.win.close(), "kullanıcı 'Hayır' derse pencere açık kalmalı")
        q.assert_called_once()
        self.assertTrue(self.win.isVisible())

    @unittest.skipIf(os.name == "nt", "sembolik bağ için yönetici izni gerekebilir")
    def test_gallery_symlink_never_deletes_its_target(self):
        # Galeri kaynağı (ör. bir arşiv uygulaması) sembolik bağ yazsa bile yalnızca bağ silinmeli
        target = os.path.join(self.tmp, "onemli.jpg")
        make_photo(target)
        gallery = tempfile.mkdtemp(prefix="metaclean-galeri-", dir=self.tmp)
        link = os.path.join(gallery, "IMG_1.jpg")
        os.symlink(target, link)
        self.win.signals.received.emit(link)
        self.wait()
        self.assertTrue(os.path.isfile(target), "bağın gösterdiği kullanıcı dosyası silinmemeli")
        self.assertFalse(os.path.lexists(link))

    def test_heic_gets_thumbnail(self):
        # Qt HEIC okuyamaz; iPhone fotoğrafının küçük resmi pillow-heif ile çözülmeli
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()
        src = os.path.join(self.tmp, "IMG_1.heic")
        im = Image.new("RGB", (200, 100), (200, 30, 30))
        ex = im.getexif()
        ex[0x010F], ex[0x0112] = "Apple", 6  # 90° döndürülmüş çekim
        im.save(src, exif=ex.tobytes())
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertTrue(card.waiting, card.status.text())
        pix = card.thumb.pixmap()
        self.assertFalse(pix is None or pix.isNull(), "HEIC küçük resmi gösterilmeli")

    def test_video_gets_thumbnail(self):
        # 1 saniyeden kısa video da (ilk kareye düşer) küçük resim almalı
        import subprocess
        ffmpeg = tools.find_tool("ffmpeg")
        for name, dur in (("uzun.mp4", "2"), ("kisa.mp4", "0.4")):
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=320x240:rate=10",
                            "-metadata", "title=gizli", "-pix_fmt", "yuv420p", os.path.join(self.tmp, name)], check=True)
        self.win.add_paths([os.path.join(self.tmp, n) for n in ("uzun.mp4", "kisa.mp4")])
        self.wait()
        for card in self.win.cards.values():
            self.assertTrue(card.waiting, card.status.text())
            pix = card.thumb.pixmap()
            self.assertFalse(pix is None or pix.isNull(), f"{card.job.path}: video küçük resmi gösterilmeli")

    @unittest.skipIf(os.name == "nt", "Windows dosya adında < ve > kabul etmez; bu durum orada oluşamaz")
    def test_file_name_shown_as_plain_text(self):
        src = os.path.join(self.tmp, "<u>alti-cizili<u>.jpg")  # dosya adında "/" olamaz
        make_photo(src)
        self.win.add_paths([src])
        card = next(iter(self.win.cards.values()))
        from PySide6.QtCore import Qt
        self.assertEqual(card.name.textFormat(), Qt.PlainText)
        self.assertEqual(card.name.text(), "<u>alti-cizili<u>.jpg")
        self.wait()

    def test_parallel_setting_respected(self):
        # Gerileme: ana pencere Ayarlar'daki "aynı anda işlenecek dosya" sayısını yok sayıyordu
        from PySide6.QtCore import QSettings
        from metaclean.gui import dialogs
        store = QSettings(os.path.join(self.tmp, "ayar.ini"), QSettings.IniFormat)
        store.setValue("jobs/parallel", 5)
        with mock.patch.object(dialogs, "settings", lambda: store):
            win = self.sw.SimpleWindow()
        self.assertEqual(win.pool.maxThreadCount(), 5)
        win.close()

    def test_english_interface(self):
        from metaclean import i18n
        i18n.set_language("en")
        try:
            win = self.sw.SimpleWindow()
            src = os.path.join(self.tmp, "a.jpg")
            make_photo(src)
            win.add_paths([src])
            t = time.time()
            while any(not c.done for c in win.cards.values()) and time.time() - t < 60:
                self.app.processEvents()
                time.sleep(0.02)
            card = next(iter(win.cards.values()))
            text = self.card_text(card)
            for expected in ("Clean copy ready", "Location", "Device information"):
                self.assertIn(expected, text)
            self.assertEqual(card.btn_save.text(), "Save")
            self.assertIn("Location (coordinates)", win.meta.body.toPlainText())
            self.assertIn("Removed when saved", win.meta.body.toPlainText())
            for c in win.cards.values():
                c.discard()
            win.close()
        finally:
            i18n.set_language("tr")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "chmod 000 sınanamıyor")
    def test_unreadable_file_does_not_abort_folder_drop(self):
        # Gerileme: klasördeki tek okunamayan dosya bütün bırakmayı PermissionError ile durduruyordu
        folder = os.path.join(self.tmp, "klasor")
        os.makedirs(folder)
        make_photo(os.path.join(folder, "iyi.jpg"))
        bad = os.path.join(folder, "okunamaz.jpg")
        make_photo(bad)
        os.chmod(bad, 0)
        try:
            self.win.add_paths([folder])
        finally:
            os.chmod(bad, 0o600)
        self.assertEqual([os.path.basename(c.job.path) for c in self.win.cards.values()], ["iyi.jpg"])
        self.wait()

    def test_thumbnail_only_for_recognised_formats(self):
        # Güvenilmez dosya, tanınan biçimin çözücüsü dışında hiçbir çözücüye verilmemeli (ör. EPS -> Ghostscript)
        eps = os.path.join(self.tmp, "belge.eps")
        with open(eps, "w") as f:
            f.write("%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n")
        self.assertTrue(self.sw.pil_thumbnail(eps).isNull())
        jpg = os.path.join(self.tmp, "a.jpg")
        make_photo(jpg)
        self.assertTrue(self.sw.pil_thumbnail(jpg).isNull(), "Pillow yolu yalnızca HEIF/AVIF içindir")
        self.win.add_paths([eps])
        self.wait()
        card = next(iter(self.win.cards.values()))
        pix = card.thumb.pixmap()
        self.assertTrue(pix is None or pix.isNull(), "tanınmayan dosyaya küçük resim üretilmemeli")

    def test_video_frame_respects_cancel(self):
        import threading
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.sw.video_frame(os.path.join(self.tmp, "yok.mp4"), cancel=stop), b"")

    @unittest.skipUnless(sys.platform == "darwin", "macOS'a özgü")
    def test_system_language_from_macos_not_lang_env(self):
        # Gerileme: Finder'dan açılan uygulamada LANG yok; Qt "C" görüyor, Türkçe Mac'te uygulama İngilizce açılıyordu
        from Foundation import NSLocale
        from metaclean.gui import langsetup
        from PySide6.QtCore import QLocale
        with mock.patch.object(QLocale, "system", return_value=QLocale.c()):
            langs = langsetup.system_languages()
        self.assertEqual(langs[0], str(NSLocale.preferredLanguages()[0]))
        self.assertNotIn("C", langs)

    def test_language_switch_applies_immediately(self):
        from PySide6.QtCore import QSettings
        from metaclean import i18n
        from metaclean.gui import dialogs
        store = QSettings(os.path.join(self.tmp, "ayar.ini"), QSettings.IniFormat)
        store.setValue("ui/lang", "en")
        try:
            with mock.patch.object(dialogs, "settings", lambda: store):
                self.win.switch_language()
            new = self.app._metaclean_window
            self.assertFalse(self.win.isVisible(), "eski pencere kapanmalı")
            self.assertTrue(new.isVisible())
            self.assertEqual(new.drop.findChildren(self.sw.QPushButton)[0].text(), "Choose file")
            self.assertTrue(self.app.quitOnLastWindowClosed(), "uygulamanın normal kapanma davranışı geri gelmeli")
            new.close()
        finally:
            i18n.set_language("tr")

    def test_log_never_contains_metadata_values(self):
        # Gerileme: başarısız temizlik kapısının ayrıntıları ("Kalan alan: ... = değer") kayda yazılıyordu
        from metaclean.core.handlers import images
        orig_clean = images.JpegHandler.clean

        def leaky(self_, ctx):
            out = orig_clean(self_, ctx)
            with open(out, "rb") as f:
                data = f.read()
            secret = "Ayşe Yılmaz Kadıköy".encode("utf-8")
            com = b"\xff\xfe" + (len(secret) + 2).to_bytes(2, "big") + secret
            with open(out, "wb") as f:
                f.write(data[:2] + com + data[2:])
            return out

        src = os.path.join(self.tmp, "a.jpg")
        make_photo(src)
        with mock.patch.object(images.JpegHandler, "clean", leaky), self.assertLogs("metaclean.gui", "INFO") as logs:
            self.win.add_paths([src])
            self.wait()
        text = "\n".join(logs.output)
        self.assertIn("başarısız kapılar", text)
        self.assertNotIn("Kadıköy", text, "kayda meta veri değeri yazılmamalı")

    def test_unsupported_file_explained(self):
        src = os.path.join(self.tmp, "belge.jpg")
        with open(src, "w", encoding="utf-8") as f:
            f.write("bu bir resim değil")
        self.win.add_paths([src])
        self.wait()
        card = next(iter(self.win.cards.values()))
        self.assertIsNone(card.dest)
        self.assertIn("desteklenmiyor", card.status.text())
        self.win.show_meta(card)
        self.assertNotIn("bulunmadı", self.win.meta.subtitle.text(), "temizlenemeyen dosyada yanlış güvence verilmemeli")
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
        self.assertTrue(self.win.btn_save_all.isVisibleTo(self.win))
        self.win.btn_save_all.click()
        self.wait()
        names = sorted(os.path.basename(c.dest) for c in self.win.cards.values())
        self.assertEqual(names, ["foto-1.jpg", "foto-2.jpg", "foto-3.jpg", "foto-4.jpg"])


if __name__ == "__main__":
    unittest.main()
