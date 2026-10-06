"""Ayarlar, Araçlar ve Sınırlar pencereleri."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTextBrowser,
                               QVBoxLayout)

from .. import __version__
from ..core import tools
from ..core.handlers.base import Options
from ..i18n import tr

INSTALL_HINTS = {
    "darwin": "brew install exiftool ffmpeg",
    "win32": "winget install OliverBetz.ExifTool Gyan.FFmpeg",
    "linux": "sudo apt install libimage-exiftool-perl ffmpeg   (Fedora: sudo dnf install perl-Image-ExifTool ffmpeg)",
}


def settings() -> QSettings:
    return QSettings("MetaClean", "MetaClean")


def saved_options() -> Options:
    return Options(full_video_decode=settings().value("verify/full", False, bool))


def saved_parallel() -> int:
    return settings().value("jobs/parallel", 2, int)


def saved_language() -> str:
    """"auto" (sistem dili), "tr" ya da "en"."""
    return settings().value("ui/lang", "auto", str)


def about_html() -> str:
    from ..applog import log_dir
    return (f"<h2>MetaClean {__version__}</h2>"
            + tr("<p>Fotoğraf, ses ve videolardaki gizli bilgileri kaliteyi bozmadan kaldırır.</p>"
                 "<p>İnternete bağlanmaz, veri toplamaz. Kayıt dosyasına dosya adı ya da yolu yazılmaz.</p>"
                 "<p>Kayıt klasörü: <code>{folder}</code></p>"
                 "<p>İçinde gelen araçlar: ExifTool (Phil Harvey), FFmpeg, Qt (PySide6), Pillow, mutagen, "
                 "pillow-heif. Lisans bildirimleri: <code>THIRD_PARTY_NOTICES.md</code></p>", folder=log_dir()))


def apply_tool_overrides() -> None:
    s = settings()
    for t in tools.TOOLS:
        tools.set_override(t, s.value(f"tools/{t}", "", str) or None)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Ayarlar"))
        self.language_changed = False
        self.setMinimumWidth(560)
        s = settings()
        lay = QVBoxLayout(self)

        lg = QGroupBox("Dil / Language")  # iki dilde: yanlış dilde kalan da bulabilsin
        ll = QHBoxLayout(lg)
        self.lang = QComboBox()
        for code, name in (("auto", tr("Otomatik (sistem dili)")), ("tr", "Türkçe"), ("en", "English")):
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(saved_language())))
        ll.addWidget(self.lang, 1)
        lay.addWidget(lg)

        vg = QGroupBox(tr("Doğrulama"))
        vl = QVBoxLayout(vg)
        self.full = QCheckBox(tr("Tam doğrulama: videoyu kare kare çöz ve karşılaştır (yavaş)"))
        self.full.setChecked(s.value("verify/full", False, bool))
        vl.addWidget(self.full)
        n = QLabel(tr("Kapalıyken video, sıkıştırılmış paketlerin özeti ve zamanlamasıyla karşılaştırılır. Bu da "
                      "bit düzeyinde eşitliği gösterir. Görsel ve ses dosyaları her zaman tam çözülür."))
        n.setWordWrap(True)
        n.setStyleSheet("color: palette(placeholder-text);")
        vl.addWidget(n)
        par = QHBoxLayout()
        par.addWidget(QLabel(tr("Aynı anda işlenecek dosya:")))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.setValue(s.value("jobs/parallel", 2, int))
        par.addWidget(self.parallel)
        par.addStretch(1)
        vl.addLayout(par)
        lay.addWidget(vg)

        tg = QGroupBox(tr("Araç yolları (boş: otomatik bul)"))
        form = QFormLayout(tg)
        self.paths = {}
        for t in tools.TOOLS:
            edit = QLineEdit(s.value(f"tools/{t}", "", str))
            tools.set_override(t, None)
            edit.setPlaceholderText(tools.find_tool(t) or tr("bulunamadı"))
            apply_tool_overrides()
            btn = QPushButton(tr("Seç…"))
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
        path, _ = QFileDialog.getOpenFileName(self, tr("{name} konumu", name=name))
        if path:
            edit.setText(path)

    def _save(self) -> None:
        s = settings()
        lang = self.lang.currentData()
        self.language_changed = lang != saved_language()  # pencere yeni dille hemen yeniden kurulur
        if self.language_changed:
            s.setValue("ui/lang", lang)
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
        rows.append(f"<tr><td><b>{mark} {t}</b></td><td>{ver or '—'}</td><td><code>{path or tr('bulunamadı')}</code></td></tr>")
    libs = []
    for mod, what in (("PIL", tr("Pillow (görsel çözme)")), ("mutagen", tr("mutagen (ses etiketleri)")),
                      ("pillow_heif", tr("pillow-heif (HEIC/AVIF çözme)"))):
        try:
            m = __import__(mod)
            libs.append(f"<li>✓ {what} {getattr(m, '__version__', '')}</li>")
        except ImportError:
            libs.append(f"<li>✗ {what} – <code>pip install {what.split()[0].lower()}</code></li>")
    hint = INSTALL_HINTS.get(sys.platform if sys.platform in INSTALL_HINTS else "linux")
    return (f"<h3>{tr('Harici araçlar')}</h3><table cellpadding=4>{''.join(rows)}</table>"
            f"<p>{tr('Kurulum')}: <code>{hint}</code></p><h3>{tr('Python kütüphaneleri')}</h3><ul>{''.join(libs)}</ul>"
            + tr("<p>ExifTool her formatta yeniden tarama için, FFmpeg/ffprobe ses ve video için gereklidir.</p>"))


def limits_html() -> str:
    return tr(LIMITS_HTML)


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
<li>Orijinal dosyaya hiç dokunulmaz; temiz sürüm her zaman ayrı bir kopyadır. Orijinali paylaşmayın ya da
kendiniz silin.</li>
<li>Bulut eşitleme sürüm geçmişi (iCloud, Google Drive, OneDrive, Dropbox), yedekler (Time Machine, Dosya Geçmişi)
ve küçük resim önbellekleri programın erişemediği kopyalardır.</li>
<li>HEIC içindeki küçük resim ve derinlik haritası öğeleri meta veri değil görüntü öğesidir; ExifTool silemez.
Bunlar varsa uyarı gösterilir.</li>
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
