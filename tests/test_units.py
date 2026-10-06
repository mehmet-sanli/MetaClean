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
        self.assertEqual(out["Tarih ve saat"], "15 Ağustos 2026, 14:22", "kartta da panel gibi okunur tarih")

    def test_gps_position_formatted(self):
        out = self.summarize([("GPS", "GPSLatitude", "x")], scan={"Composite:GPSPosition": "41 deg 0' 29.50\" N, 28 deg 58' 42.00\" E"})
        self.assertEqual(out["Konum"], "41°0'29.50\" N, 28°58'42.00\" E")

    def test_generic_exif_text_is_not_thumbnail(self):
        # Gerileme: EXIF bloğunun açıklamasında "küçük resim" geçtiği için yanlış etiketleniyordu
        out = self.summarize([("XMP-dc", "Subject", "Photo Booth")],
                             removed=["EXIF (APP1) – kamera, tarih, GPS, küçük resim, üretici notları, 62 bayt"])
        self.assertNotIn("Küçük resim / kapak", out)

    def test_generic_exif_text_is_not_location(self):
        # Gerileme: GPS'siz fotoğrafın kartında "Konum kaldırıldı" yazıyordu
        out = self.summarize([("XMP-x", "XMPToolkit", "XMP Core 6.0.0")],
                             removed=["EXIF (APP1) – kamera, tarih, GPS, küçük resim, üretici notları, 202 bayt"])
        self.assertNotIn("Konum", out)
        self.assertIn("Konum", self.summarize([], removed=["İz 3: Kamera hareket/GPS izi (camm)"]))

    def test_readable_labels_and_values(self):
        scan = {"IFD0:Make": "Canon", "ExifIFD:DateTimeOriginal": "2026:08:15 14:22:33",
                "GPS:GPSLatitudeRef": "North", "GPS:GPSLatitude": "41 deg 0' 29.50\" N",
                "Composite:GPSPosition": "41 deg 0' 29.50\" N, 28 deg 58' 42.00\" E",
                "XMP-iptcExt:DigitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
                "ICC_Profile:ProfileDescription": "Display P3", "System:FileName": "IMG_1.jpg"}
        out = {title: rows for _, title, rows in categories.readable(scan)}
        self.assertEqual(out["Cihaz bilgisi"], [("Cihaz markası", "Canon", "silinir")])
        self.assertEqual(out["Tarih ve saat"], [("Çekim tarihi", "15 Ağustos 2026, 14:22", "silinir")])
        self.assertEqual(out["Konum"], [("Konum (koordinat)", "41°0'29.50\" N, 28°58'42.00\" E", "silinir")])
        self.assertEqual(out["İçerik kaynağı"][0][1], "Yapay zekâ ile üretilmiş")
        self.assertIn(("Renk profili", "Display P3", "korunur"), out["Korunan (kişisel değil)"])
        self.assertNotIn("IMG_1.jpg", str(out))  # dosya sistemi bilgisi listelenmez

    def test_video_location_single_readable_row(self):
        # iPhone videosu konumu iki alanda taşır (Keys ISO 6709 + UserData 3GPP); tek ve okunur satır olmalı
        scan = {"Keys:Location": "+41.0082+028.9784/",
                "UserData:LocationInformation": "(none) Role=shooting Lat=41.00819 Lon=28.97839 Alt=0.00 Body=earth"}
        konum = {t: rows for _, t, rows in categories.readable(scan)}["Konum"]
        self.assertEqual(konum, [("Konum (koordinat)", "41°0'29.52\" N, 28°58'42.24\" E", "silinir")])

    def test_no_location_means_no_location_section(self):
        out = {t for _, t, _ in categories.readable({"IFD0:Make": "Canon", "ExifIFD:ColorSpace": "sRGB"})}
        self.assertNotIn("Konum", out, "konum yoksa konumla ilgili hiçbir şey gösterilmemeli")

    def test_place_name_shown_as_place(self):
        konum = {t: rows for _, t, rows in categories.readable({"Vorbis:Location": "İstanbul"})}["Konum"]
        self.assertEqual(konum, [("Yer", "İstanbul", "silinir")])

    def test_real_thumbnail_and_cover(self):
        self.assertIn("Küçük resim / kapak", self.summarize([], removed=["JFIF küçük resmi (300 bayt)"]))
        self.assertIn("Küçük resim / kapak", self.summarize([], removed=["İz 1: kapak resmi (mjpeg)"]))

    def test_creator_tool_is_history_not_person(self):
        self.assertEqual(categories.categorize("XMP-xmp", "CreatorTool"), "gecmis")

    def test_numeric_values_hidden(self):
        out = self.summarize([("XMP-dc", "Rating", "5")])
        self.assertEqual(out["Açıklama ve etiketler"], "")


class I18nTests(unittest.TestCase):
    """Dil desteği: her metnin İngilizcesi var, mantık görünen dile bağlı değil."""

    def tearDown(self):
        from metaclean import i18n
        i18n.set_language("tr")

    @staticmethod
    def translatable_keys():
        """Koddaki tr("…") literalleri ve tablolar üzerinden tr()'ye giden metinler."""
        import ast
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metaclean")
        keys = set()
        for folder, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py") and f != "i18n_en.py":
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        tree = ast.parse(fh.read())
                    keys |= {n.args[0].value for n in ast.walk(tree)
                             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr" and n.args
                             and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
        from metaclean.core.handlers import audio, images, video
        keys |= set(categories.LABELS.values()) | set(categories.VALUES.values()) | set(categories._MONTHS)
        keys |= {t for _, _, t in categories.CATEGORIES} | {categories.COORD_LABEL}
        keys |= set(images.PNG_NAMES.values()) | set(video.DATA_NAMES.values()) | set(audio.FLAC_NAMES.values())
        return keys

    def test_every_text_has_english(self):
        from metaclean.i18n_en import EN
        missing = sorted(k for k in self.translatable_keys() if k not in EN)
        self.assertEqual(missing, [], "İngilizce karşılığı olmayan metinler")

    def test_placeholders_match(self):
        import string
        from metaclean.i18n_en import EN
        fields = lambda t: sorted(f for _, f, _, _ in string.Formatter().parse(t) if f)
        self.assertEqual([k for k, v in EN.items() if fields(k) != fields(v)], [])

    def test_translation_keeps_turkish_source(self):
        from metaclean import i18n
        i18n.set_language("en")
        t = i18n.tr("{name}, {n} bayt", name=i18n.tr("JFXX / APP0 küçük resmi"), n=12)
        self.assertEqual(t, "JFXX / APP0 thumbnail, 12 bytes")
        self.assertEqual(i18n.source(t), "JFXX / APP0 küçük resmi, 12 bayt", "iç içe metin de Türkçe aslıyla")

    def test_language_resolution(self):
        from metaclean.i18n import resolve
        self.assertEqual(resolve("auto", ["tr-TR", "en-US"]), "tr")
        self.assertEqual(resolve("auto", ["de-DE"]), "en", "Türkçe olmayan her sistem İngilizce")
        self.assertEqual(resolve("auto", []), "en")
        self.assertEqual(resolve("tr", ["en-US"]), "tr", "ayardaki seçim sistemi geçersiz kılar")

    def test_logic_independent_of_language(self):
        # İngilizce modda da kart etiketleri Türkçe asla bakarak doğru konmalı
        from metaclean import i18n
        i18n.set_language("en")
        r = Report(path="x")
        r.removed = [i18n.tr("JFIF küçük resmi ({n} bayt)", n=300),
                     i18n.tr("İz {i}: {name} [{tag}]", i=2, name=i18n.tr("Kamera hareket/GPS izi (camm)"), tag="camm")]
        titles = [t for _, t, _ in categories.summarize(r)]
        self.assertEqual(titles, ["Location", "Thumbnail / cover"])
        self.assertEqual(categories.pretty_value("2026:08:15 14:22:33"), "15 August 2026, 14:22")


class StabilityTests(unittest.TestCase):
    """Güvenlik/kararlılık incelemesinde bulunan hataların gerilemesi."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="metaclean-kararlilik-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _id3(self, size: int) -> bytes:
        ss = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
        return b"ID3\x04\x00\x00" + ss + b"\x00" * size

    def test_large_id3_tag_still_detected(self):
        # Gerileme: 8 KB'tan büyük ID3 (gömülü kapak resmi) MP3/FLAC'ı "desteklenmeyen" yapıyordu
        from metaclean.core import detect
        mp3_frame = b"\xff\xfb\x90\x64" + b"\x00" * 400
        for name, body, fmt in (("a.mp3", self._id3(250_000) + mp3_frame, "mp3"),
                                ("b.flac", self._id3(60_000) + b"fLaC" + b"\x00" * 100, "flac"),
                                ("c.mp3", self._id3(20) + self._id3(30_000) + mp3_frame, "mp3")):
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as f:
                f.write(body)
            self.assertEqual(detect.detect(path), fmt, name)

    def test_decompression_bomb_refused(self):
        # Birkaç baytlık ama 30000x30000 piksel olduğunu söyleyen PNG belleği tüketmeden reddedilmeli
        import struct
        import zlib
        from PIL import Image
        from metaclean.core.handlers import images
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
        path = os.path.join(self.tmp, "bomba.png")
        with open(path, "wb") as f:
            f.write(png)
        with self.assertRaises(Image.DecompressionBombError):
            images.decode_digest(path)

    def test_stale_workdirs_cleaned_but_fresh_kept(self):
        # Çökmeden kalan geçici klasörler (galeri kopyaları konum taşıyabilir) açılışta silinir; yenilere dokunulmaz
        from metaclean.core import session
        old = tempfile.mkdtemp(prefix="metaclean-galeri-")
        fresh = tempfile.mkdtemp(prefix="metaclean-galeri-")
        os.utime(old, (0, 0))
        try:
            session.cleanup_stale_workdirs()
            self.assertFalse(os.path.exists(old), "bir günden eski klasör silinmeli")
            self.assertTrue(os.path.exists(fresh), "yeni klasöre (başka açık MetaClean) dokunulmamalı")
        finally:
            import shutil
            shutil.rmtree(fresh, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)

    def test_timestamp_failure_does_not_fail_save(self):
        from metaclean.core import timestamps
        path = os.path.join(self.tmp, "x.bin")
        open(path, "wb").close()
        with mock.patch.object(timestamps.os, "utime", side_effect=PermissionError("kilitli")):
            notes = timestamps.apply(path)
        self.assertTrue(any(n.startswith("✗") for n in notes), notes)

    @unittest.skipIf(os.name == "nt", "Windows'ta Unix izin bitleri yok; chmod yalnızca salt-okunur bayrağıdır")
    def test_tool_finder_does_not_chmod_user_files(self):
        from metaclean.core import tools
        path = os.path.join(self.tmp, "exiftool-sahte")
        with open(path, "w") as f:
            f.write("calistirilamaz")
        os.chmod(path, 0o644)
        tools.set_override("exiftool", path)
        try:
            tools.find_tool("exiftool")
        finally:
            tools.set_override("exiftool", None)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644, "kullanıcının dosyasının iznine dokunulmamalı")

    def test_frame_parameter_translated(self):
        from metaclean import i18n
        from metaclean.core.handlers import images
        i18n.set_language("en")
        try:
            self.assertEqual(images._param_label("kare 3"), "frame 3")
            self.assertEqual(images._param_label("yön"), "orientation")
        finally:
            i18n.set_language("tr")


class FetchToolsTests(unittest.TestCase):
    """Pakete giren ExifTool/FFmpeg sabit sürümdür ve parmak izi doğrulanır."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging"))
        import fetch_tools
        self.ft = fetch_tools

    def test_tampered_download_stops_build(self):
        import io
        with mock.patch.object(self.ft.urllib.request, "urlopen", return_value=io.BytesIO(b"degistirilmis")):
            with self.assertRaisesRegex(SystemExit, "SHA-256 tutmadı"):
                self.ft.get(self.ft.FFMPEG_WIN)

    def test_matching_download_accepted(self):
        import hashlib
        import io
        data = b"dogru icerik"
        source = ("https://ornek/arac.zip", hashlib.sha256(data).hexdigest())
        with mock.patch.object(self.ft.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            self.assertEqual(self.ft.get(source), data)

    def test_every_tool_pinned(self):
        sources = [self.ft.EXIFTOOL_UNIX, self.ft.EXIFTOOL_WIN, self.ft.FFMPEG_WIN, self.ft.FFMPEG_LINUX,
                   *self.ft.FFMPEG_MAC.values()]
        for url, digest in sources:
            self.assertRegex(digest, r"^[0-9a-f]{64}$", url)
            self.assertNotIn("latest", url, "'en son' adresi sabit değildir")


class GalleryDropTests(unittest.TestCase):
    def test_when_promises_are_used(self):
        from metaclean.gui.macdrop import use_promises
        lib = "/Users/a/Pictures/Photos Library.photoslibrary/resources/derivatives/x.jpeg"
        self.assertTrue(use_promises([], True), "yol yok, söz var: galeriden al")
        self.assertTrue(use_promises([lib], True), "kütüphane önizlemesi yerine orijinali al")
        self.assertFalse(use_promises(["/Users/a/Desktop/a.jpg"], True), "Finder yolu öncelikli")
        self.assertFalse(use_promises([], False))
        self.assertFalse(use_promises([lib], False), "söz yoksa eski açıklama gösterilir")


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



if __name__ == "__main__":
    unittest.main()
