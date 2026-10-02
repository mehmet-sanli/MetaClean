"""Ayarlar, Araçlar ve Sınırlar pencereleri."""
from __future__ import annotations

import sys

from PySide6.QtCore import QDateTime, QSettings
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                               QSpinBox, QTextBrowser, QVBoxLayout)

from .. import __version__
from ..core import tools

INSTALL_HINTS = {
    "darwin": "brew install exiftool ffmpeg",
    "win32": "winget install OliverBetz.ExifTool Gyan.FFmpeg",
    "linux": "sudo apt install libimage-exiftool-perl ffmpeg   (Fedora: sudo dnf install perl-Image-ExifTool ffmpeg)",
}


def settings() -> QSettings:
    return QSettings("MetaClean", "MetaClean")


def about_html() -> str:
    from ..applog import log_dir
    return (f"<h2>MetaClean {__version__}</h2>"
            "<p>Fotoğraf, ses ve videolardaki gizli bilgileri kaliteyi bozmadan kaldırır.</p>"
            "<p>İnternete bağlanmaz, veri toplamaz. Kayıt dosyasına dosya adı ya da yolu yazılmaz.</p>"
            f"<p>Kayıt klasörü: <code>{log_dir()}</code></p>"
            "<p>İçinde gelen araçlar: ExifTool (Phil Harvey), FFmpeg, Qt (PySide6), Pillow, mutagen, "
            "pillow-heif. Lisans bildirimleri: <code>THIRD_PARTY_NOTICES.md</code></p>")


def apply_tool_overrides() -> None:
    s = settings()
    for t in tools.TOOLS:
        tools.set_override(t, s.value(f"tools/{t}", "", str) or None)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ayarlar")
        self.setMinimumWidth(560)
        s = settings()
        lay = QVBoxLayout(self)

        ts = QGroupBox("Kaydettikten sonra zaman damgaları")
        tl = QVBoxLayout(ts)
        self.now = QRadioButton("Kaydetme anı (önerilen; her platformda sağlanabilen tek seçenek)")
        self.fixed = QRadioButton("Sabit tarih:")
        grp = QButtonGroup(self)
        grp.addButton(self.now)
        grp.addButton(self.fixed)
        self.when = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(s.value("ts/fixed", 1767225600, int))))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        row = QHBoxLayout()
        row.addWidget(self.fixed)
        row.addWidget(self.when, 1)
        tl.addWidget(self.now)
        tl.addLayout(row)
        note = QLabel("Değiştirilme, erişim ve oluşturulma zamanı seçilen ana çekilir ve geri okunarak doğrulanır. "
                      "Kaydetme anı, temizliğin ne zaman yapıldığını ele verir. Linux'ta oluşturulma zamanı "
                      "ayarlanamaz; sabit tarih orada yalnızca değiştirilme ve erişim zamanına uygulanır.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        tl.addWidget(note)
        (self.fixed if s.value("ts/mode", "now") == "fixed" else self.now).setChecked(True)
        self.when.setEnabled(self.fixed.isChecked())
        self.fixed.toggled.connect(self.when.setEnabled)
        lay.addWidget(ts)

        vg = QGroupBox("Doğrulama")
        vl = QVBoxLayout(vg)
        self.full = QCheckBox("Tam doğrulama: videoyu kare kare çöz ve karşılaştır (yavaş)")
        self.full.setChecked(s.value("verify/full", False, bool))
        vl.addWidget(self.full)
        n = QLabel("Kapalıyken video, sıkıştırılmış paketlerin özeti ve zamanlamasıyla karşılaştırılır. Bu da "
                   "bit düzeyinde eşitliği gösterir. Görsel ve ses dosyaları her zaman tam çözülür.")
        n.setWordWrap(True)
        n.setStyleSheet("color: palette(placeholder-text);")
        vl.addWidget(n)
        par = QHBoxLayout()
        par.addWidget(QLabel("Aynı anda işlenecek dosya:"))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.setValue(s.value("jobs/parallel", 2, int))
        par.addWidget(self.parallel)
        par.addStretch(1)
        vl.addLayout(par)
        lay.addWidget(vg)

        tg = QGroupBox("Araç yolları (boş: otomatik bul)")
        form = QFormLayout(tg)
        self.paths = {}
        for t in tools.TOOLS:
            edit = QLineEdit(s.value(f"tools/{t}", "", str))
            tools.set_override(t, None)
            edit.setPlaceholderText(tools.find_tool(t) or "bulunamadı")
            apply_tool_overrides()
            btn = QPushButton("Seç…")
            btn.clicked.connect(lambda _=False, e=edit, name=t: self._browse(e, name))
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(btn)
            form.addRow(t, row)
            self.paths[t] = edit
        lay.addWidget(tg)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self, edit: QLineEdit, name: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"{name} konumu")
        if path:
            edit.setText(path)

    def _save(self) -> None:
        s = settings()
        s.setValue("ts/mode", "fixed" if self.fixed.isChecked() else "now")
        s.setValue("ts/fixed", self.when.dateTime().toSecsSinceEpoch())
        s.setValue("verify/full", self.full.isChecked())
        s.setValue("jobs/parallel", self.parallel.value())
        for t, e in self.paths.items():
            s.setValue(f"tools/{t}", e.text().strip())
        apply_tool_overrides()
        self.accept()


def tools_html() -> str:
    rows = []
    for t in tools.TOOLS:
        path = tools.find_tool(t)
        ver = tools.tool_version(t) if path else None
        mark = "✓" if path else "✗"
        rows.append(f"<tr><td><b>{mark} {t}</b></td><td>{ver or '—'}</td><td><code>{path or 'bulunamadı'}</code></td></tr>")
    libs = []
    for mod, what in (("PIL", "Pillow (görsel çözme)"), ("mutagen", "mutagen (ses etiketleri)"),
                      ("pillow_heif", "pillow-heif (HEIC/AVIF çözme)")):
        try:
            m = __import__(mod)
            libs.append(f"<li>✓ {what} {getattr(m, '__version__', '')}</li>")
        except ImportError:
            libs.append(f"<li>✗ {what} – <code>pip install {what.split()[0].lower()}</code></li>")
    hint = INSTALL_HINTS.get(sys.platform if sys.platform in INSTALL_HINTS else "linux")
    return (f"<h3>Harici araçlar</h3><table cellpadding=4>{''.join(rows)}</table>"
            f"<p>Kurulum: <code>{hint}</code></p><h3>Python kütüphaneleri</h3><ul>{''.join(libs)}</ul>"
            "<p>ExifTool her formatta yeniden tarama için, FFmpeg/ffprobe ses ve video için gereklidir.</p>")


LIMITS_HTML = """
<h3>Nasıl çalışır</h3>
<p>Medya hiçbir zaman yeniden kodlanmaz. JPEG, PNG, WebP, MP3 ve FLAC'ta meta veri blokları doğrudan atılır,
sıkıştırılmış veri bayt bayt kopyalanır. HEIC/AVIF'te bunu ExifTool yapar. MP4, MOV, M4A, MKV ve WebM
FFmpeg ile <code>-c copy</code> remux edilir; izler tek tek seçilir.</p>
<p>Format başına bir izin listesi var: yalnızca doğru görüntüleme/çalma için gereken alanlar kalır
(EXIF yönü, ICC profili, Adobe APP14, döndürme matrisi, HDR/Dolby Vision, Xing/LAME, iTunSMPB,
FLAC kanal maskesi, ASS altyazı yazı tipleri). Listede olmayan her şey silinir.</p>
<h3>Üç kapı</h3>
<ol><li><b>Bütünlük:</b> temiz dosya baştan sona hatasız çözülüyor mu?</li>
<li><b>Eşdeğerlik:</b> ham piksel/PCM ya da sıkıştırılmış paket özetleri ve teknik parametreler aynı mı?</li>
<li><b>Temizlik:</b> yapısal denetim ve ExifTool yeniden taraması izin listesi dışında alan buluyor mu?</li></ol>
<p>Biri bile geçilmezse geçici dosya silinir ve kaydetme seçeneği sunulmaz.</p>
<h3>Programın çözemedikleri</h3>
<ul>
<li>Üzerine yazılan eski dosyanın blokları diskte bir süre kurtarılabilir kalır. SSD'de güvenli silme garanti edilemez.</li>
<li>Bulut eşitleme sürüm geçmişi (iCloud, Google Drive, OneDrive, Dropbox), yedekler (Time Machine, Dosya Geçmişi)
ve küçük resim önbellekleri programın erişemediği kopyalardır.</li>
<li>HEIC içindeki küçük resim ve derinlik haritası öğeleri meta veri değil görüntü öğesidir; ExifTool silemez.
Bunlar varsa uyarı gösterilir.</li>
<li>Sabit bağlı (hard link) dosyada 'Kaydet' yalnızca seçilen adı değiştirir.</li>
<li>Görüntünün içeriği (yüzler, tabelalar, ekran yansımaları) meta veri değildir.</li>
<li>RAW dosyaları (CR2, NEF, ARW…) kapsam dışıdır; üretici notları görüntünün doğru açılması için gerekebilir.</li>
</ul>
<h3>Yan fayda</h3>
<p>Dosya yeni oluşturulduğu için NTFS'teki Zone.Identifier (indirildiği adres), macOS'taki kMDItemWhereFroms,
karantina bayrağı ve Finder etiketleri gibi dosya dışı izler de kaybolur.</p>
"""


class InfoDialog(QDialog):
    def __init__(self, title: str, html: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setHtml(html)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
