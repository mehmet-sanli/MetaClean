"""Basit mod: paylaşmadan önce dosyayı bırak, temiz kopyayı al."""
from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from itertools import count
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, QStandardPaths, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..core import detect, tools
from ..core.categories import summarize
from ..core.handlers import images
from ..core.session import Job, neutral_name
from ..i18n import source, tr
from . import dialogs, langsetup, macdrop, metapanel
from .metaview import MetadataView

log = logging.getLogger("metaclean.gui")

OUTPUT_DIR_NAME = "Paylaşıma Hazır"
SUPPORTED_TEXT = "Fotoğraf (JPEG, PNG, WebP, HEIC, AVIF), video (MP4, MOV, MKV, WebM) ve ses (MP3, FLAC, M4A)"
KIND_ICON = {"image": "🖼", "video": "🎬", "audio": "🎵"}

ACCENT, ACCENT_DARK = "#12a26f", "#0b7a63"
ASSETS = os.path.join(os.path.dirname(__file__), "assets")

STYLE = """
QMainWindow, #root { background: palette(window); }
#brand { font-size: 24px; font-weight: 700; }
#tagline { color: palette(placeholder-text); font-size: 13px; }
#drop { border: 2px dashed rgba(18,162,111,0.55); border-radius: 18px; background: palette(base); }
#drop:hover { border-color: #12a26f; background: rgba(18,162,111,0.05); }
#drop[active="true"] { border: 2px solid #12a26f; background: rgba(18,162,111,0.12); }
#dropText { font-size: 18px; font-weight: 650; }
#dropHint { color: palette(placeholder-text); font-size: 12px; }
#steps { background: transparent; }
#stepNum { color: white; background: #12a26f; border-radius: 14px; font-weight: 700; }
#stepTitle { font-weight: 600; font-size: 13px; }
#stepText { color: palette(placeholder-text); font-size: 12px; }
#card { border: 1px solid rgba(127,127,127,0.25); border-radius: 14px; background: palette(base); }
#card[selected="true"] { border: 2px solid #12a26f; }
#metaPanel { border: 1px solid rgba(127,127,127,0.25); border-radius: 14px; background: palette(base); }
#metaTitle { font-size: 14px; font-weight: 650; }
#metaSub { color: palette(placeholder-text); font-size: 12px; }
#cardName { font-size: 14px; font-weight: 650; }
#cardStatus { font-size: 13px; }
#chip { border-radius: 9px; padding: 4px 10px; background: rgba(18,162,111,0.10); }
#tip { color: palette(placeholder-text); font-size: 12px; padding: 8px 10px;
       background: rgba(127,127,127,0.08); border-radius: 10px; }
QPushButton#primary { background: #12a26f; color: white; border: none; border-radius: 9px;
                      padding: 9px 18px; font-weight: 650; font-size: 13px; }
QPushButton#primary:hover { background: #0b7a63; }
QPushButton#link { border: none; color: #12a26f; background: transparent; padding: 4px 6px; font-weight: 600; }
QPushButton#link:hover { color: #0b7a63; text-decoration: underline; }
QPushButton#menu { border: 1px solid rgba(127,127,127,0.35); border-radius: 9px; padding: 6px 12px;
                   background: palette(base); }
QPushButton#menu::menu-indicator { image: none; width: 0; }
QProgressBar { border: none; border-radius: 3px; background: rgba(127,127,127,0.18); }
QProgressBar::chunk { border-radius: 3px; background: #12a26f; }
QScrollArea { background: transparent; }
"""


def logo_pixmap(size: int) -> QPixmap:
    """Logoyu SVG'den istenen boyutta, ekran ölçeğine göre keskin çizer."""
    from PySide6.QtGui import QGuiApplication, QPainter
    from PySide6.QtSvg import QSvgRenderer
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 2.0
    px = int(size * ratio)
    pix = QPixmap(px, px)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(os.path.join(ASSETS, "logo.svg")).render(painter)
    painter.end()
    pix.setDevicePixelRatio(ratio)
    return pix



def output_dir() -> str:
    desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or os.path.expanduser("~")
    return os.path.join(desktop, tr(OUTPUT_DIR_NAME))


# Küçük resim yalnızca uygulamanın tanıdığı biçimlerden ve yalnızca o biçimin çözücüsüyle üretilir: dosya
# güvenilmez olabilir; Qt'nin ve Pillow'un bütün çözücülerine (ör. Ghostscript çağıran EPS) verilmez.
QT_THUMB_FORMATS = {"jpeg": b"jpeg", "png": b"png", "webp": b"webp"}
PIL_THUMB_FORMATS = {"heif": "HEIF", "avif": "AVIF"}


def pil_thumbnail(path: str, size: int = 176) -> QImage:
    """Qt'nin okuyamadığı biçimler (iPhone HEIC, AVIF) için küçük resmi pillow-heif ile çözer."""
    try:
        from PIL import ImageOps
        Image = images._pil()
        Image.init()
        allowed = [f for f in PIL_THUMB_FORMATS.values() if f in Image.OPEN]
        with Image.open(path, formats=allowed) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            im = im.convert("RGBA")
            return QImage(im.tobytes(), im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
    except Exception:  # noqa: BLE001 - küçük resim yoksa simge kalır
        return QImage()


def video_frame(path: str, size: int = 176, cancel=None) -> bytes:
    """Videonun 1. saniyesindeki kareyi (kısa videoda ilk kareyi) PNG olarak döndürür; diske yazılmaz.
    cancel: iş iptal edilir ya da pencere kapanırsa takılan ffmpeg durdurulur."""
    for seek in (["-ss", "1"], []):
        try:
            cp = tools.run([tools.require("ffmpeg"), "-nostdin", "-v", "error", *seek, "-i", path, "-frames:v", "1",
                            "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                            "-f", "image2pipe", "-c:v", "png", "-"], cancel=cancel)
        except (tools.ToolError, tools.Cancelled):
            return b""
        if cp.returncode == 0 and cp.stdout:
            return cp.stdout
    return b""


def _supported(path: str) -> bool:
    """Klasör taranırken: okunamayan tek bir dosya bütün bırakma işlemini durdurmasın."""
    try:
        return detect.detect(path) is not None
    except OSError:
        return False


def reveal(path: str) -> None:
    """Dosyayı Finder / Gezgin içinde seçili olarak gösterir."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def friendly_error(job: Job) -> str:
    r = job.report
    err = r.error or ""
    if r.error_kind == "unsupported":
        return tr("Bu dosya türü desteklenmiyor.\nDesteklenenler: {formats}.", formats=tr(SUPPORTED_TEXT))
    if r.error_kind == "missing_tool":
        return tr("Gerekli bir bileşen eksik (ExifTool ya da FFmpeg). Sağ üstteki “Diğer” menüsünden “Araçlar”a bakın.")
    if r.error_kind == "cancelled":
        return tr("İptal edildi.")
    if r.gates and not r.ok:
        return tr("Bu dosya güvenle temizlenemedi. Kaliteyi bozmamak ya da bilgi kaçırmamak için kopya "
                  "oluşturulmadı. Teknik ayrıntılar için “Ayrıntılar”a tıklayın.")
    return tr("Dosya işlenemedi: {err}", err=err)


class _Signals(QObject):
    stage = Signal(int, object)  # Text: Türkçe aslı (source) aşama eşlemesinde kullanılır
    scanned = Signal(int, object)
    thumb = Signal(int, bytes)           # videodan çıkarılan küçük resim (PNG)
    done = Signal(int, object)           # hazırlık bitti: (kart, hata)
    saved = Signal(int, object, object)  # kaydetme bitti: (kart, hedef, hata)
    received = Signal(str)         # galeriden (dosya sözüyle) gelen dosya hazır
    receive_failed = Signal(str)


class _Task(QRunnable):
    """İncele -> 3 kapı. Temiz kopya yalnızca sistemin geçici klasöründe hazırlanır; kullanıcı
    "Kaydet"e basmadan hiçbir yere yazılmaz. Orijinale dokunmaz."""

    def __init__(self, cid: int, job: Job, signals: _Signals):
        super().__init__()
        self.cid, self.job, self.signals = cid, job, signals

    def run(self) -> None:
        scan = None
        try:
            # Temizlikten önce oku: kullanıcı dosyada ne olduğunu hemen görsün (prepare aynı taramayı kullanır)
            scan = tools.exiftool_scan(self.job.real, cancel=self.job.cancel)
            self.signals.scanned.emit(self.cid, scan)
        except Exception:  # noqa: BLE001 - asıl hata aşağıda prepare'de raporlanır
            pass
        try:
            r = self.job.prepare(lambda text: self.signals.stage.emit(self.cid, text), scan)
            if self.job.ready and detect.kind(r.fmt) == "video":
                # Kare çıkarmak birkaç yüz ms sürebilir; pencere donmasın diye burada, arka planda
                self.signals.thumb.emit(self.cid, video_frame(self.job.out, cancel=self.job.cancel))
            self.signals.done.emit(self.cid, None)
        except Exception as e:  # noqa: BLE001 - kartta gösterilir
            self.job.discard()
            self.signals.done.emit(self.cid, e)


class _SaveTask(QRunnable):
    """Kullanıcı "Kaydet"e bastıktan sonra: temiz kopyayı masaüstündeki Paylaşıma Hazır klasörüne yazar."""

    def __init__(self, cid: int, job: Job, signals: _Signals):
        super().__init__()
        self.cid, self.job, self.signals = cid, job, signals

    def run(self) -> None:
        dest = None
        try:
            folder = output_dir()
            os.makedirs(folder, exist_ok=True)
            dest = neutral_name(folder, self.job.report.fmt)
            self.job.save_copy(dest)
            self.signals.saved.emit(self.cid, dest, None)
        except Exception as e:  # noqa: BLE001 - kartta gösterilir
            if dest and os.path.exists(dest) and os.path.getsize(dest) == 0:
                os.remove(dest)  # ayrılan ama yazılamayan ad
            self.signals.saved.emit(self.cid, None, e)


# Kullanıcıya gösterilen sade aşama adları
STAGE_TEXT = [
    ("SHA-256", "Dosya okunuyor…"), ("taranıyor", "Gizli bilgiler aranıyor…"), ("okunuyor", "Gizli bilgiler aranıyor…"),
    ("Temizleniyor", "Gizli bilgiler kaldırılıyor…"), ("Remux", "Gizli bilgiler kaldırılıyor…"),
    ("Bütünlük", "Kalite kontrol ediliyor…"), ("Eşdeğerlik", "Kalite kontrol ediliyor…"),
    ("Temizlik", "Son kontrol yapılıyor…"),
]


def stage_text(raw) -> str:
    raw = source(raw)  # görünen dile değil Türkçe aslına bakılır
    return tr(next((t for k, t in STAGE_TEXT if k in raw), "Hazırlanıyor…"))


class ResultCard(QFrame):
    def __init__(self, window: "SimpleWindow", cid: int, job: Job, from_gallery: bool = False):
        super().__init__()
        self.window, self.cid, self.job, self.dest = window, cid, job, None
        self.done = False
        self.scan: Optional[dict] = None
        self.from_gallery = from_gallery  # orijinal, galeriden alınmış geçici bir kopya; işten sonra silinir
        self.found_items = False
        self.discarded = False
        self.saving = False
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Meta verisini aşağıda görmek için tıklayın"))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setFixedSize(QSize(88, 88))
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("font-size: 38px; border-radius: 8px; background: palette(alternate-base);")
        lay.addWidget(self.thumb, 0, Qt.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(6)
        self.name = QLabel(os.path.basename(job.path))
        self.name.setTextFormat(Qt.PlainText)  # dosya adı biçimlendirme (HTML) olarak yorumlanmasın
        self.name.setObjectName("cardName")
        self.status = QLabel(tr("Sırada…"))
        self.status.setObjectName("cardStatus")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.chips = QWidget()
        self.chips_lay = QVBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips_lay.setSpacing(3)
        self.chips.hide()
        self.note = QLabel()
        self.note.setObjectName("tip")
        self.note.setWordWrap(True)
        self.note.hide()
        body.addWidget(self.name)
        body.addWidget(self.status)
        body.addWidget(self.bar)
        body.addWidget(self.chips)
        body.addWidget(self.note)
        buttons = QHBoxLayout()
        self.btn_save = QPushButton(tr("Kaydet"))
        self.btn_save.setObjectName("primary")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setToolTip(tr("Temiz kopyayı masaüstündeki “{folder}” klasörüne kaydeder", folder=tr(OUTPUT_DIR_NAME)))
        self.btn_save.clicked.connect(lambda: self.window.save_card(self))
        self.btn_save.hide()
        self.btn_discard = QPushButton(tr("Vazgeç"))
        self.btn_discard.setObjectName("link")
        self.btn_discard.setToolTip(tr("Hiçbir şey kaydetmeden temiz kopyayı at"))
        self.btn_discard.clicked.connect(self.discard)
        self.btn_discard.hide()
        self.btn_show = QPushButton(tr("Temiz kopyayı göster"))
        self.btn_show.setObjectName("primary")
        self.btn_show.setCursor(Qt.PointingHandCursor)
        self.btn_show.clicked.connect(lambda: reveal(self.dest))
        self.btn_show.hide()
        self.btn_details = QPushButton(tr("Ayrıntılar"))
        self.btn_details.setObjectName("link")
        self.btn_details.setCursor(Qt.PointingHandCursor)
        self.btn_details.clicked.connect(self.open_details)
        self.btn_details.hide()
        self.btn_cancel = QPushButton(tr("İptal"))
        self.btn_cancel.setObjectName("link")
        self.btn_cancel.clicked.connect(self.cancel)
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_show)
        buttons.addWidget(self.btn_discard)
        buttons.addWidget(self.btn_details)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)
        body.addLayout(buttons)
        lay.addLayout(body, 1)
        self._set_icon()

    def _set_icon(self) -> None:
        try:
            fmt = detect.detect(self.job.real)
        except OSError:
            fmt = None
        self.thumb.setText(KIND_ICON.get(detect.kind(fmt), "📄") if fmt else "📄")

    def _load_thumbnail(self, path: str) -> None:
        fmt = self.job.report.fmt  # imzadan tanınan biçim; tanınmayan dosyaya küçük resim yok
        if fmt in QT_THUMB_FORMATS:
            reader = QImageReader(path, QT_THUMB_FORMATS[fmt])
            reader.setAutoTransform(True)  # EXIF yönüne göre çevir
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(QSize(176, 176), Qt.KeepAspectRatio))
            self.show_image(reader.read())
        elif fmt in PIL_THUMB_FORMATS:
            self.show_image(pil_thumbnail(path))

    def show_image(self, img: QImage) -> None:
        if not img.isNull():
            pix = QPixmap.fromImage(img).scaled(QSize(88, 88), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.thumb.setPixmap(pix.copy((pix.width() - 88) // 2, (pix.height() - 88) // 2, 88, 88))

    def set_stage(self, raw: str) -> None:
        self.status.setText(stage_text(raw))

    def finish(self, error) -> None:
        """Hazırlık bitti: temiz kopya doğrulandıysa onay bekler, hiçbir şey kaydedilmez."""
        self.done = True
        self.bar.hide()
        self.btn_cancel.hide()
        self.btn_details.show()
        r = self.job.report
        if self.job.ready:
            self.status.setText(f"<b style='color:#2e9e5b'>✅ {tr('Temiz kopya hazır')}</b> &nbsp;·&nbsp; "
                                f"<span>{tr('henüz kaydedilmedi; kaydetmek için “Kaydet”e basın')}</span>")
            self._load_thumbnail(self.job.out)
            self.btn_save.show()
            self.btn_discard.show()
            items = summarize(r)
            self.found_items = bool(items)
            if items:
                self.chips_head = QLabel(f"<b>{tr('Kaydedince silinecek gizli bilgiler:')}</b>")
                self.chips_lay.addWidget(self.chips_head)
                for icon, title, value in items:
                    text = f"{icon} <b>{html.escape(title)}</b>"
                    if value:
                        text += f" <span style='color:gray'>— {html.escape(value)}</span>"
                    lbl = QLabel(text)
                    lbl.setObjectName("chip")
                    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    self.chips_lay.addWidget(lbl)
            else:
                self.chips_lay.addWidget(QLabel(tr("Dosyada kişisel bilgi bulunmadı; isterseniz yine de temiz kopyayı "
                                                   "kaydedebilirsiniz.")))
            self.chips.show()
            notes = [w for w in r.warnings if "küçük resim öğesi" in source(w) or "derinlik" in source(w)]
            if self.from_gallery:
                notes.append(tr("Galeriden alınan geçici kopya silindi; galerideki fotoğrafa dokunulmadı."))
            if notes:
                self.note.setText("⚠ " + " ".join(notes))
                self.note.show()
        else:
            self._load_thumbnail(self.job.real)
            msg = friendly_error(self.job) if error is None else tr("Dosya işlenemedi: {err}", err=error)
            self.status.setText(f"<b style='color:#d64545'>✗ {tr('Temiz kopya oluşturulamadı')}</b><br>{html.escape(msg)}"
                                .replace("\n", "<br>"))
            if not r.scan:
                self.btn_details.hide()

    def start_saving(self) -> None:
        self.saving = True
        self.btn_save.setEnabled(False)
        self.btn_discard.hide()
        self.status.setText(tr("Kaydediliyor…"))

    def saved(self, dest: Optional[str], error) -> None:
        self.saving = False
        self.btn_save.setEnabled(True)
        if dest:
            self.dest = dest
            self.btn_save.hide()
            self.btn_discard.hide()
            self.btn_show.show()
            self.status.setText(f"<b style='color:#2e9e5b'>✅ {tr('Kaydedildi')}</b> &nbsp;·&nbsp; "
                                f"<span>{html.escape(tr(OUTPUT_DIR_NAME))} / {html.escape(os.path.basename(dest))}</span>")
            if self.found_items:
                self.chips_head.setText(f"<b>{tr('Kaldırılan gizli bilgiler:')}</b>")
        else:
            self.btn_discard.show()
            self.status.setText(f"<b style='color:#d64545'>✗ {tr('Kaydedilemedi')}</b><br>{html.escape(str(error))}")

    def discard(self) -> None:
        self.job.discard()
        self.discarded = True
        self.btn_save.hide()
        self.btn_discard.hide()
        self.status.setText(f"<b>{tr('Vazgeçildi')}</b> &nbsp;·&nbsp; {tr('hiçbir dosya kaydedilmedi')}")
        self.window.card_changed(self)

    @property
    def waiting(self) -> bool:
        """Temiz kopya hazır, kullanıcının onayını bekliyor."""
        return self.done and not self.dest and not self.discarded and self.job.ready

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def meta_state(self) -> str:
        if not self.done:
            return metapanel.FOUND if self.scan else metapanel.READING
        if self.dest:
            return metapanel.CLEANED
        if self.discarded:
            return metapanel.DISCARDED
        return metapanel.READY if self.job.ready else metapanel.FAILED

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window.show_meta(self)
        super().mouseReleaseEvent(e)

    def cancel(self) -> None:
        self.job.cancel.set()
        self.status.setText(tr("İptal ediliyor…"))

    def open_details(self) -> None:
        DetailsDialog(self.job, self.dest, self.window, from_gallery=self.from_gallery).exec()


class DetailsDialog(QDialog):
    """Meraklı kullanıcı için: tüm meta veri ve doğrulama kanıtı."""

    def __init__(self, job: Job, dest: Optional[str], parent=None, from_gallery: bool = False):
        super().__init__(parent)
        r = job.report
        self.setWindowTitle(tr("Ayrıntılar – {name}", name=os.path.basename(job.path)))
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        meta = MetadataView()
        meta.set_scans(r.scan, r.clean_scan or None, cleanable=bool(r.fmt))
        tabs.addTab(meta, tr("Gizli bilgiler ({count})", count=meta.count_text()))
        proof = QTextBrowser()
        esc = html.escape
        if from_gallery:
            parts = [tr("<p><b>Orijinal:</b> galeriden (ör. Fotoğraflar) alındı. Galerideki fotoğrafa dokunulmadı; "
                        "işlem için alınan geçici kopya silindi.</p>")]
        else:
            parts = [tr("<p><b>Orijinal:</b> {path}<br>Orijinal dosyaya dokunulmadı.</p>", path=esc(job.real))]
        if dest:
            parts.append(tr("<p><b>Temiz kopya:</b> {path}</p>", path=esc(dest)))
        if r.gates:
            parts.append(f"<h3>{tr('Kalite ve temizlik kontrolleri')}</h3>")
            for g in r.gates:
                c = "#2e9e5b" if g.passed else "#d64545"
                parts.append(f"<p><b style='color:{c}'>{'✓' if g.passed else '✗'} {esc(g.name)}</b></p><ul>"
                             + "".join(f"<li>{esc(d)}</li>" for d in g.details) + "</ul>")
        if r.removed:
            parts.append(f"<h3>{tr('Kaldırılanlar')}</h3><ul>" + "".join(f"<li>{esc(x)}</li>" for x in r.removed) + "</ul>")
        if r.kept:
            parts.append(f"<h3>{tr('Bilerek bırakılanlar')}</h3><ul>"
                         + "".join(f"<li><b>{esc(k.what)}</b> — {esc(k.reason)}</li>" for k in r.kept) + "</ul>")
        for w in r.warnings:
            parts.append(f"<p style='color:#c98a00'>⚠ {esc(w)}</p>")
        if r.error:
            parts.append(f"<p style='color:#d64545'>{esc(r.error)}</p>")
        proof.setHtml("".join(parts))
        tabs.addTab(proof, tr("Kontroller"))
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class DropArea(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("drop")
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 26, 24, 24)
        lay.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(logo_pixmap(64))
        text = QLabel(tr("Fotoğraf ya da videoyu buraya sürükleyin"))
        text.setObjectName("dropText")
        hint = QLabel(tr(SUPPORTED_TEXT))
        hint.setObjectName("dropHint")
        hint.setWordWrap(True)
        self.icon, self.hint, self.lay = icon, hint, lay
        for w in (icon, text):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)
        btn = QPushButton(tr("Dosya seç"))
        btn.setObjectName("primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(on_click)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

    def set_compact(self, on: bool) -> None:
        """Liste doluyken yer açmak için simgesiz, ince görünüm."""
        self.icon.setVisible(not on)
        self.hint.setVisible(not on)
        self.lay.setContentsMargins(24, 10 if on else 26, 24, 10 if on else 24)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click()


class SimpleWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MetaClean")
        self.resize(780, 820)
        self.setMinimumSize(560, 520)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        self.pool = QThreadPool(self)
        # Aynı anda işlenecek dosya: çekirdeklerin yarısı, 2-4 arası (büyük videolar belleği zorlamasın)
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 2) // 2)))
        self.signals = _Signals()
        self.signals.stage.connect(lambda cid, t: self.cards[cid].set_stage(t) if cid in self.cards else None)
        self.signals.thumb.connect(lambda cid, png: self.cards[cid].show_image(QImage.fromData(png))
                                   if cid in self.cards else None)
        self.signals.scanned.connect(self._on_scanned)
        self.signals.done.connect(self._on_done)
        self.signals.saved.connect(self._on_saved)
        self.signals.received.connect(self._on_received)
        self.signals.receive_failed.connect(self._on_receive_failed)
        self.meta_card: Optional[ResultCard] = None
        self._gallery_dirs: List[str] = []
        self._receiving = 0
        self._drag_promises = False
        log.info("galeriden sürükleme (dosya sözü) desteği: %s", macdrop.AVAILABLE)
        self.cards: Dict[int, ResultCard] = {}
        self._ids = count(1)

        root = QWidget()
        root.setObjectName("root")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(48))
        head.addWidget(logo, 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("MetaClean")
        title.setObjectName("brand")
        sub = QLabel(tr("Paylaşmadan önce konum, cihaz ve tarih bilgisini kaliteyi bozmadan kaldırır. "
                        "Orijinal dosyanız olduğu gibi kalır."))
        sub.setObjectName("tagline")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        more = QPushButton(tr("Diğer") + "  ▾")
        more.setObjectName("menu")
        more.setCursor(Qt.PointingHandCursor)
        menu_btn_menu = self._build_menu()
        more.setMenu(menu_btn_menu)
        head.addWidget(more, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background: rgba(201,138,0,0.15); border: 1px solid #c98a00; "
                                  "border-radius: 8px; padding: 10px;")
        self.banner.hide()
        lay.addWidget(self.banner)

        self.drop = DropArea(self.choose_files)
        lay.addWidget(self.drop)
        self.receiving = QLabel()
        self.receiving.setObjectName("tip")
        self.receiving.hide()
        lay.addWidget(self.receiving)

        self.steps = QWidget()
        self.steps.setObjectName("steps")
        st = QHBoxLayout(self.steps)
        st.setContentsMargins(4, 6, 4, 0)
        st.setSpacing(18)
        for n, head_text, body in (("1", tr("Bırakın"), tr("Dosyayı yukarıya sürükleyin ya da seçin.")),
                                   ("2", tr("Temizlensin"), tr("Konum, cihaz, tarih ve isim bilgileri kaldırılır.")),
                                   ("3", tr("Kaydedin"), tr("“Kaydet”e basınca temiz kopya masaüstündeki “{folder}” "
                                                            "klasörüne kaydedilir.", folder=tr(OUTPUT_DIR_NAME)))):
            col = QHBoxLayout()
            col.setSpacing(10)
            num = QLabel(n)
            num.setObjectName("stepNum")
            num.setFixedSize(28, 28)
            num.setAlignment(Qt.AlignCenter)
            texts = QVBoxLayout()
            texts.setSpacing(1)
            t1 = QLabel(head_text)
            t1.setObjectName("stepTitle")
            t2 = QLabel(body)
            t2.setObjectName("stepText")
            t2.setWordWrap(True)
            texts.addWidget(t1)
            texts.addWidget(t2)
            col.addWidget(num, 0, Qt.AlignTop)
            col.addLayout(texts, 1)
            st.addLayout(col, 1)
        lay.addWidget(self.steps)

        self.list_header = QHBoxLayout()
        self.list_title = QLabel("")
        self.list_title.setStyleSheet("font-weight: 600;")
        self.btn_folder = QPushButton(tr("📂 {folder} klasörünü aç", folder=tr(OUTPUT_DIR_NAME)))
        self.btn_folder.setObjectName("link")
        self.btn_folder.clicked.connect(self.open_output)
        self.btn_save_all = QPushButton(tr("Hepsini kaydet"))
        self.btn_save_all.setObjectName("link")
        self.btn_save_all.clicked.connect(self.save_all)
        self.btn_clear = QPushButton(tr("Listeyi temizle"))
        self.btn_clear.setObjectName("link")
        self.btn_clear.clicked.connect(self.clear_list)
        self.list_header.addWidget(self.list_title)
        self.list_header.addStretch(1)
        self.list_header.addWidget(self.btn_save_all)
        self.list_header.addWidget(self.btn_folder)
        self.list_header.addWidget(self.btn_clear)
        lay.addLayout(self.list_header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.cards_lay = QVBoxLayout(holder)
        self.cards_lay.setContentsMargins(0, 0, 4, 0)
        self.cards_lay.setSpacing(10)
        self.cards_lay.addStretch(1)
        self.scroll.setWidget(holder)
        self.meta = metapanel.MetaPanel()
        self.meta.hide()
        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.scroll)
        self.split.addWidget(self.meta)
        self.split.setSizes([300, 320])
        lay.addWidget(self.split, 1)
        self.spacer = QWidget()
        self.spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay.addWidget(self.spacer, 1)

        tip = QLabel(tr("💡 MetaClean dosyanın içindeki gizli bilgileri temizler. Fotoğrafın kendisinde görünen yüz, "
                        "adres, plaka ya da ekran görüntüsündeki isimleri paylaşmadan önce siz kontrol edin."))
        tip.setObjectName("tip")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.setCentralWidget(root)
        self._update_header()
        self._check_tools()

    def _build_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(tr("Ayarlar…"), self.open_settings)
        m.addAction(tr("Araçlar…"), lambda: dialogs.InfoDialog(tr("Araçlar"), dialogs.tools_html(), self).exec())
        m.addAction(tr("Nasıl çalışır?"), lambda: dialogs.InfoDialog(tr("Nasıl çalışır"), dialogs.limits_html(), self).exec())
        m.addSeparator()
        m.addAction(tr("MetaClean hakkında"), lambda: dialogs.InfoDialog(tr("Hakkında"), dialogs.about_html(), self).exec())
        return m

    def _check_tools(self) -> None:
        missing = [t for t in tools.TOOLS if not tools.find_tool(t)]
        if missing:
            hint = dialogs.INSTALL_HINTS.get(sys.platform if sys.platform in dialogs.INSTALL_HINTS else "linux")
            self.banner.setText(tr("<b>Program tam çalışmak için bir bileşen daha istiyor ({tools}).</b><br>"
                                   "Kurmak için Terminal'e şunu yazın: <code>{hint}</code>", tools=", ".join(missing), hint=hint))
            self.banner.show()

    # ------------------------------------------------------------ ekleme
    def _has_files(self, e) -> bool:
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            return True
        return self._drag_promises  # Fotoğraflar, Safari, Mail: yol yerine dosya sözü

    def dragEnterEvent(self, e):
        log.info("dragEnter: %s", [u.toString() for u in e.mimeData().urls()])
        self._drag_promises = macdrop.has_promises()  # sürükleme başına bir kez; dragMove her harekette gelir
        if self._has_files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.drop.set_active(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._has_files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.drop.set_active(False)

    def dropEvent(self, e):
        self.drop.set_active(False)
        if not self._has_files(e):
            return
        e.setDropAction(Qt.CopyAction)
        e.accept()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        log.info("drop: %d yol, dosya sözü: %s", len(paths), self._drag_promises)
        if macdrop.use_promises(paths, self._drag_promises):
            paths = [p for p in paths if macdrop.LIBRARY_MARK not in p]  # önizleme yolları yerine orijinaller
            self._receive_from_gallery()
        if paths:
            self.add_paths(paths)

    # ------------------------------------------------------------ galeriden alma (macOS dosya sözleri)
    def _receive_from_gallery(self) -> None:
        folder = tempfile.mkdtemp(prefix="metaclean-galeri-")  # yalnızca kullanıcıya açık (0700)
        self._gallery_dirs.append(folder)
        try:
            n = macdrop.receive(folder, self.signals.received.emit, self.signals.receive_failed.emit)
        except Exception as e:  # noqa: BLE001
            log.exception("dosya sözü alınamadı")
            self._on_receive_failed(str(e))
            return
        self._receiving += n
        self._update_receiving()

    def _update_receiving(self) -> None:
        self.receiving.setText(tr("⏳ Galeriden alınıyor… ({n} dosya)", n=self._receiving))
        self.receiving.setVisible(self._receiving > 0)

    def _on_received(self, path: str) -> None:
        self._receiving = max(0, self._receiving - 1)
        self._update_receiving()
        self._add(path, from_gallery=True)

    def _on_receive_failed(self, message: str) -> None:
        self._receiving = max(0, self._receiving - 1)
        self._update_receiving()
        QMessageBox.warning(self, tr("Galeriden alınamadı"),
                            tr("Dosya galeriden alınamadı. Fotoğraf iCloud'daysa önce indirilmesini bekleyin "
                               "ya da Dosya → Dışa Aktar ile masaüstüne kaydedip oradan bırakın.\n\n{message}",
                               message=message))

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Paylaşılacak dosyaları seçin"), "",
            tr("Fotoğraf, video ve ses") + " (*.jpg *.jpeg *.png *.webp *.heic *.heif *.avif *.mp3 *.flac *.m4a *.mp4 "
            "*.mov *.mkv *.webm);;" + tr("Tüm dosyalar") + " (*)")
        self.add_paths(paths)

    def add_paths(self, paths: List[str]) -> None:
        log.info("add_paths: %s", paths)
        if any(".photoslibrary" in p for p in paths):
            QMessageBox.information(
                self, tr("Fotoğraflar uygulamasından"),
                tr("Fotoğraflar uygulamasından doğrudan sürüklenen dosya orijinal değil, küçük bir önizleme oluyor.\n\n"
                   "Şöyle yapın: Fotoğraflar'da fotoğrafı seçin → Dosya → Dışa Aktar → “Değiştirilmemiş Orijinali "
                   "Dışa Aktar” → Masaüstüne kaydedin. Sonra o dosyayı buraya bırakın."))
            paths = [p for p in paths if ".photoslibrary" not in p]
        out = os.path.realpath(output_dir())
        files = []
        for p in paths:
            if os.path.isdir(p):
                for rootdir, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files += [os.path.join(rootdir, n) for n in sorted(names) if not n.startswith(".")
                              and _supported(os.path.join(rootdir, n))]
            elif os.path.isfile(p):
                files.append(p)
        for f in files:
            if os.path.realpath(f).startswith(out + os.sep):
                continue  # zaten temizlenmiş kopyalar
            self._add(f)

    def _add(self, path: str, from_gallery: bool = False) -> None:
        cid = next(self._ids)
        job = Job(path)
        card = ResultCard(self, cid, job, from_gallery)
        self.cards[cid] = card
        self.cards_lay.insertWidget(0, card)
        self.scroll.verticalScrollBar().setValue(0)
        self._update_header()
        self.show_meta(card)
        self.pool.start(_Task(cid, job, self.signals))

    # ------------------------------------------------------------ meta veri paneli
    def show_meta(self, card: "ResultCard") -> None:
        if self.meta_card is not None and self.meta_card is not card:
            self.meta_card.set_selected(False)
        self.meta_card = card
        card.set_selected(True)
        scan = card.scan or card.job.report.scan or None
        self.meta.show_scan(os.path.basename(card.job.path), scan, card.meta_state())
        self.meta.show()

    def _on_scanned(self, cid: int, scan) -> None:
        card = self.cards.get(cid)
        if card:
            card.scan = scan
            self.card_changed(card)

    def _on_done(self, cid: int, error) -> None:
        card = self.cards.get(cid)
        if card:
            r = card.job.report
            log.info("hazır: %s hata=%s rapor=%s", card.job.path, error, r.error)
            # Kayda kontrol ayrıntıları yazılmaz: "Kalan alan: GPS:GPSLatitude = 41 deg…" gibi satırlar
            # konum ve isim değerleri taşır. Yalnızca kapı adları ve kalan alanların adları yazılır.
            failed = [source(g.name) for g in r.gates if not g.passed]
            if failed:
                log.info("  başarısız kapılar: %s, kalan alan adları: %s", failed, [f.key for f in r.remaining])
            if r.warnings:
                log.info("  uyarılar: %s", [source(w) for w in r.warnings])
            card.finish(error)
            self.card_changed(card)
            if card.from_gallery:
                # Galeriden alınan geçici orijinal konum vb. taşır; işi biter bitmez sil. Yolun kendisi
                # silinir (job.real değil): bir sembolik bağsa gösterdiği dosyaya dokunulmaz
                try:
                    os.remove(card.job.path)
                except OSError:
                    log.warning("geçici galeri kopyası silinemedi")
                try:
                    os.rmdir(os.path.dirname(card.job.path))  # aynı sürüklemeden dosya kalmadıysa klasörü de sil
                except OSError:
                    pass

    # ------------------------------------------------------------ kaydetme (yalnızca kullanıcı isteyince)
    def save_card(self, card: "ResultCard") -> None:
        if not card.waiting or card.saving:
            return
        card.start_saving()
        self.pool.start(_SaveTask(card.cid, card.job, self.signals))
        self._update_header()

    def save_all(self) -> None:
        for card in list(self.cards.values()):
            self.save_card(card)

    def _on_saved(self, cid: int, dest, error) -> None:
        card = self.cards.get(cid)
        if card:
            log.info("kaydedildi: %s -> %s hata=%s", card.job.path, dest, error)
            card.saved(dest, error)
            self.card_changed(card)

    def card_changed(self, card: "ResultCard") -> None:
        if card is self.meta_card:
            self.show_meta(card)
        self._update_header()

    def _update_header(self) -> None:
        total = len(self.cards)
        saved = sum(1 for c in self.cards.values() if c.dest)
        waiting = sum(1 for c in self.cards.values() if c.waiting and not c.saving)
        busy = sum(1 for c in self.cards.values() if not c.done or c.saving)
        parts = [tr("{n} kaydedildi", n=saved)] if saved else []
        if waiting:
            parts.append(tr("{n} onay bekliyor", n=waiting))
        if busy:
            parts.append(tr("{n} işleniyor", n=busy))
        self.list_title.setText(" · ".join(parts))
        self.btn_save_all.setText(tr("Hepsini kaydet ({n})", n=waiting))
        self.btn_save_all.setVisible(waiting > 1)
        for w in (self.btn_clear, self.list_title, self.split):
            w.setVisible(total > 0)
        self.steps.setVisible(total == 0)
        self.drop.set_compact(total > 0)
        self.spacer.setVisible(total == 0)

    def open_output(self) -> None:
        folder = output_dir()
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def clear_list(self) -> None:
        for cid, card in list(self.cards.items()):
            if not card.done or card.waiting or card.saving:
                continue  # işlenen ya da onay bekleyen kartlar listede kalır
            if card is self.meta_card:
                self.meta_card = None
                self.meta.hide()
            card.setParent(None)
            card.deleteLater()
            del self.cards[cid]
        self._update_header()

    def open_settings(self) -> None:
        dlg = dialogs.SettingsDialog(self)
        if dlg.exec() and dlg.language_changed:
            self.switch_language()

    def switch_language(self) -> None:
        """Yeni dili hemen uygular: pencere yeni dille yeniden kurulur. Kaydedilmemiş dosya varsa kapanırken
        her zamanki gibi sorulur; kullanıcı vazgeçerse dil bir sonraki açılışta geçerli olur."""
        app = QApplication.instance()
        app.setQuitOnLastWindowClosed(False)  # eski pencere kapanınca uygulama kapanmasın
        try:
            if not self.close():
                return
            langsetup.apply(app)
            app._metaclean_window = SimpleWindow()  # referans tutulmazsa pencere çöp toplanır
            app._metaclean_window.show()
        finally:
            app.setQuitOnLastWindowClosed(True)

    def closeEvent(self, event) -> None:
        waiting = sum(1 for c in self.cards.values() if c.waiting)
        if waiting and QMessageBox.question(
                self, tr("Kaydedilmemiş dosyalar"),
                tr("{n} temiz kopya henüz kaydedilmedi. Kaydetmeden çıkılsın mı?", n=waiting)) != QMessageBox.Yes:
            event.ignore()
            return
        for c in self.cards.values():
            c.job.cancel.set()
        self.pool.waitForDone(10000)
        for d in self._gallery_dirs:
            shutil.rmtree(d, ignore_errors=True)
        event.accept()
