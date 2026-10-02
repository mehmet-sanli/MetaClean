"""Basit mod: paylaşmadan önce dosyayı bırak, temiz kopyayı al."""
from __future__ import annotations

import html
import logging
import os
import subprocess
import sys
from itertools import count
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, QStandardPaths, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..core import detect, tools
from ..core.categories import summarize
from ..core.session import Job
from . import dialogs
from .metaview import MetadataView

log = logging.getLogger("metaclean.gui")

OUTPUT_DIR_NAME = "Paylaşıma Hazır"
SUPPORTED_TEXT = "Fotoğraf (JPEG, PNG, WebP, HEIC, AVIF), video (MP4, MOV, MKV, WebM) ve ses (MP3, FLAC, M4A)"
KIND_PREFIX = {"image": "foto", "video": "video", "audio": "ses"}
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
    return os.path.join(desktop, OUTPUT_DIR_NAME)


def neutral_name(folder: str, fmt: str) -> str:
    """Tarih ya da cihaz adı taşımayan ad: foto-1.jpg, video-2.mp4…"""
    prefix, ext = KIND_PREFIX[detect.kind(fmt)], detect.ext(fmt)
    n = 1
    while True:
        path = os.path.join(folder, f"{prefix}-{n}{ext}")
        try:
            # Adı hemen ayır: aynı anda işlenen iki dosya aynı adı seçip birbirini ezmesin
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            return path
        except FileExistsError:
            n += 1


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
    if err.startswith("Desteklenmeyen"):
        return f"Bu dosya türü desteklenmiyor.\nDesteklenenler: {SUPPORTED_TEXT}."
    if "bulunamadı" in err and any(t in err for t in tools.TOOLS):
        return "Gerekli bir bileşen eksik (ExifTool ya da FFmpeg). Sağ üstteki “Ayrıntılar” menüsünden “Araçlar”a bakın."
    if err == "İptal edildi.":
        return "İptal edildi."
    if r.gates and not r.ok:
        return ("Bu dosya güvenle temizlenemedi. Kaliteyi bozmamak ya da bilgi kaçırmamak için kopya "
                "oluşturulmadı. Teknik ayrıntılar için “Ayrıntılar”a tıklayın.")
    return f"Dosya işlenemedi: {err}"


class _Signals(QObject):
    stage = Signal(int, str)
    done = Signal(int, object, object)


class _Task(QRunnable):
    """İncele -> 3 kapı -> kapılar geçtiyse temiz kopyayı kaydet. Orijinale dokunmaz."""

    def __init__(self, cid: int, job: Job, signals: _Signals):
        super().__init__()
        self.cid, self.job, self.signals = cid, job, signals

    def run(self) -> None:
        try:
            r = self.job.prepare(lambda text: self.signals.stage.emit(self.cid, text))
            dest = None
            if r.ok:
                self.signals.stage.emit(self.cid, "Temiz kopya kaydediliyor")
                folder = output_dir()
                os.makedirs(folder, exist_ok=True)
                dest = neutral_name(folder, r.fmt)
                try:
                    self.job.save_copy(dest)
                except Exception:
                    if os.path.exists(dest) and os.path.getsize(dest) == 0:
                        os.remove(dest)
                    raise
            self.signals.done.emit(self.cid, dest, None)
        except Exception as e:  # noqa: BLE001 - kartta gösterilir
            self.job.discard()
            self.signals.done.emit(self.cid, None, e)


# Kullanıcıya gösterilen sade aşama adları
STAGE_TEXT = [
    ("SHA-256", "Dosya okunuyor…"), ("taranıyor", "Gizli bilgiler aranıyor…"), ("okunuyor", "Gizli bilgiler aranıyor…"),
    ("Temizleniyor", "Gizli bilgiler kaldırılıyor…"), ("Remux", "Gizli bilgiler kaldırılıyor…"),
    ("Bütünlük", "Kalite kontrol ediliyor…"), ("Eşdeğerlik", "Kalite kontrol ediliyor…"),
    ("Temizlik", "Son kontrol yapılıyor…"), ("kaydediliyor", "Kaydediliyor…"),
]


def stage_text(raw: str) -> str:
    return next((t for k, t in STAGE_TEXT if k in raw), "Hazırlanıyor…")


class ResultCard(QFrame):
    def __init__(self, window: "SimpleWindow", job: Job):
        super().__init__()
        self.window, self.job, self.dest = window, job, None
        self.done = False
        self.setObjectName("card")
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
        self.name.setObjectName("cardName")
        self.status = QLabel("Sırada…")
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
        self.btn_show = QPushButton("Temiz kopyayı göster")
        self.btn_show.setObjectName("primary")
        self.btn_show.setCursor(Qt.PointingHandCursor)
        self.btn_show.clicked.connect(lambda: reveal(self.dest))
        self.btn_show.hide()
        self.btn_details = QPushButton("Ayrıntılar")
        self.btn_details.setObjectName("link")
        self.btn_details.setCursor(Qt.PointingHandCursor)
        self.btn_details.clicked.connect(self.open_details)
        self.btn_details.hide()
        self.btn_cancel = QPushButton("İptal")
        self.btn_cancel.setObjectName("link")
        self.btn_cancel.clicked.connect(self.cancel)
        buttons.addWidget(self.btn_show)
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
        reader = QImageReader(path)
        reader.setAutoTransform(True)  # EXIF yönüne göre çevir
        if not reader.canRead():
            return
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(QSize(176, 176), Qt.KeepAspectRatio))
        img = reader.read()
        if not img.isNull():
            pix = QPixmap.fromImage(img).scaled(QSize(88, 88), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.thumb.setPixmap(pix.copy((pix.width() - 88) // 2, (pix.height() - 88) // 2, 88, 88))

    def set_stage(self, raw: str) -> None:
        self.status.setText(stage_text(raw))

    def finish(self, dest: Optional[str], error) -> None:
        self.done = True
        self.bar.hide()
        self.btn_cancel.hide()
        self.btn_details.show()
        r = self.job.report
        if dest:
            self.dest = dest
            self.status.setText(f"<b style='color:#2e9e5b'>✅ Paylaşıma hazır</b> &nbsp;·&nbsp; "
                                f"<span>{html.escape(OUTPUT_DIR_NAME)} / {html.escape(os.path.basename(dest))}</span>")
            self._load_thumbnail(dest)
            self.btn_show.show()
            items = summarize(r)
            if items:
                head = QLabel("<b>Kaldırılan gizli bilgiler:</b>")
                self.chips_lay.addWidget(head)
                for icon, title, value in items:
                    text = f"{icon} <b>{html.escape(title)}</b>"
                    if value:
                        text += f" <span style='color:gray'>— {html.escape(value)}</span>"
                    lbl = QLabel(text)
                    lbl.setObjectName("chip")
                    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    self.chips_lay.addWidget(lbl)
            else:
                self.chips_lay.addWidget(QLabel("Dosyada kişisel bilgi bulunmadı; yine de temiz bir kopya oluşturuldu."))
            self.chips.show()
            notes = [w for w in r.warnings if "küçük resim öğesi" in w or "derinlik" in w]
            if notes:
                self.note.setText("⚠ " + " ".join(notes))
                self.note.show()
        else:
            self._load_thumbnail(self.job.real)
            msg = friendly_error(self.job) if error is None else f"Dosya işlenemedi: {error}"
            self.status.setText(f"<b style='color:#d64545'>✗ Temiz kopya oluşturulamadı</b><br>{html.escape(msg)}"
                                .replace("\n", "<br>"))
            if not r.scan:
                self.btn_details.hide()

    def cancel(self) -> None:
        self.job.cancel.set()
        self.status.setText("İptal ediliyor…")

    def open_details(self) -> None:
        DetailsDialog(self.job, self.dest, self.window).exec()


class DetailsDialog(QDialog):
    """Meraklı kullanıcı için: tüm meta veri ve doğrulama kanıtı."""

    def __init__(self, job: Job, dest: Optional[str], parent=None):
        super().__init__(parent)
        r = job.report
        self.setWindowTitle(f"Ayrıntılar – {os.path.basename(job.path)}")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        meta = MetadataView()
        meta.set_scans(r.scan, r.clean_scan or None, cleanable=bool(r.fmt))
        tabs.addTab(meta, f"Gizli bilgiler ({meta.count_text()})")
        proof = QTextBrowser()
        esc = html.escape
        parts = [f"<p><b>Orijinal:</b> {esc(job.real)}<br>Orijinal dosyaya dokunulmadı.</p>"]
        if dest:
            parts.append(f"<p><b>Temiz kopya:</b> {esc(dest)}</p>")
        if r.gates:
            parts.append("<h3>Kalite ve temizlik kontrolleri</h3>")
            for g in r.gates:
                c = "#2e9e5b" if g.passed else "#d64545"
                parts.append(f"<p><b style='color:{c}'>{'✓' if g.passed else '✗'} {esc(g.name)}</b></p><ul>"
                             + "".join(f"<li>{esc(d)}</li>" for d in g.details) + "</ul>")
        if r.removed:
            parts.append("<h3>Kaldırılanlar</h3><ul>" + "".join(f"<li>{esc(x)}</li>" for x in r.removed) + "</ul>")
        if r.kept:
            parts.append("<h3>Bilerek bırakılanlar</h3><ul>"
                         + "".join(f"<li><b>{esc(k.what)}</b> — {esc(k.reason)}</li>" for k in r.kept) + "</ul>")
        for w in r.warnings:
            parts.append(f"<p style='color:#c98a00'>⚠ {esc(w)}</p>")
        if r.error:
            parts.append(f"<p style='color:#d64545'>{esc(r.error)}</p>")
        proof.setHtml("".join(parts))
        tabs.addTab(proof, "Kontroller")
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
        text = QLabel("Fotoğraf ya da videoyu buraya sürükleyin")
        text.setObjectName("dropText")
        hint = QLabel(SUPPORTED_TEXT)
        hint.setObjectName("dropHint")
        hint.setWordWrap(True)
        for w in (icon, text):
            w.setAlignment(Qt.AlignCenter)
            lay.addWidget(w)
        btn = QPushButton("Dosya seç")
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
        self.setWindowTitle("MetaClean – Paylaşmadan önce temizle")
        self.resize(760, 720)
        self.setMinimumSize(560, 520)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        dialogs.apply_tool_overrides()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = _Signals()
        self.signals.stage.connect(lambda cid, t: self.cards[cid].set_stage(t) if cid in self.cards else None)
        self.signals.done.connect(self._on_done)
        self.cards: Dict[int, ResultCard] = {}
        self._ids = count(1)
        self.advanced = None

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
        sub = QLabel("Paylaşmadan önce konum, cihaz ve tarih bilgisini kaliteyi bozmadan kaldırır. "
                     "Orijinal dosyanız olduğu gibi kalır.")
        sub.setObjectName("tagline")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        more = QPushButton("Diğer  ▾")
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

        self.steps = QWidget()
        self.steps.setObjectName("steps")
        st = QHBoxLayout(self.steps)
        st.setContentsMargins(4, 6, 4, 0)
        st.setSpacing(18)
        for n, head_text, body in (("1", "Bırakın", "Dosyayı yukarıya sürükleyin ya da seçin."),
                                   ("2", "Temizlensin", "Konum, cihaz, tarih ve isim bilgileri kaldırılır."),
                                   ("3", "Paylaşın", "Temiz kopya masaüstündeki “Paylaşıma Hazır” klasöründe.")):
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
        self.btn_folder = QPushButton("📂 Paylaşıma Hazır klasörünü aç")
        self.btn_folder.setObjectName("link")
        self.btn_folder.clicked.connect(self.open_output)
        self.btn_clear = QPushButton("Listeyi temizle")
        self.btn_clear.setObjectName("link")
        self.btn_clear.clicked.connect(self.clear_list)
        self.list_header.addWidget(self.list_title)
        self.list_header.addStretch(1)
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
        lay.addWidget(self.scroll, 1)
        self.spacer = QWidget()
        self.spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        lay.addWidget(self.spacer, 1)

        tip = QLabel("💡 MetaClean dosyanın içindeki gizli bilgileri temizler. Fotoğrafın kendisinde görünen yüz, "
                     "adres, plaka ya da ekran görüntüsündeki isimleri paylaşmadan önce siz kontrol edin.")
        tip.setObjectName("tip")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.setCentralWidget(root)
        self._update_header()
        self._check_tools()

    def _build_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Gelişmiş mod (orijinalin üzerine yazma, toplu işlem)…", self.open_advanced)
        m.addAction("Ayarlar…", lambda: dialogs.SettingsDialog(self).exec())
        m.addAction("Araçlar…", lambda: dialogs.InfoDialog("Araçlar", dialogs.tools_html(), self).exec())
        m.addAction("Nasıl çalışır?", lambda: dialogs.InfoDialog("Nasıl çalışır", dialogs.LIMITS_HTML, self).exec())
        return m

    def _check_tools(self) -> None:
        missing = [t for t in tools.TOOLS if not tools.find_tool(t)]
        if missing:
            hint = dialogs.INSTALL_HINTS.get(sys.platform if sys.platform in dialogs.INSTALL_HINTS else "linux")
            self.banner.setText(f"<b>Program tam çalışmak için bir bileşen daha istiyor ({', '.join(missing)}).</b><br>"
                                f"Kurmak için Terminal'e şunu yazın: <code>{hint}</code>")
            self.banner.show()

    # ------------------------------------------------------------ ekleme
    def _has_files(self, e) -> bool:
        return e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls())

    def dragEnterEvent(self, e):
        log.info("dragEnter: %s", [u.toString() for u in e.mimeData().urls()])
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
        if self._has_files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Paylaşılacak dosyaları seçin", "",
            "Fotoğraf, video ve ses (*.jpg *.jpeg *.png *.webp *.heic *.heif *.avif *.mp3 *.flac *.m4a *.mp4 "
            "*.mov *.mkv *.webm);;Tüm dosyalar (*)")
        self.add_paths(paths)

    def add_paths(self, paths: List[str]) -> None:
        log.info("add_paths: %s", paths)
        if any(".photoslibrary" in p for p in paths):
            QMessageBox.information(
                self, "Fotoğraflar uygulamasından",
                "Fotoğraflar uygulamasından doğrudan sürüklenen dosya orijinal değil, küçük bir önizleme oluyor.\n\n"
                "Şöyle yapın: Fotoğraflar'da fotoğrafı seçin → Dosya → Dışa Aktar → “Değiştirilmemiş Orijinali "
                "Dışa Aktar” → Masaüstüne kaydedin. Sonra o dosyayı buraya bırakın.")
            paths = [p for p in paths if ".photoslibrary" not in p]
        out = os.path.realpath(output_dir())
        files = []
        for p in paths:
            if os.path.isdir(p):
                for rootdir, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files += [os.path.join(rootdir, n) for n in sorted(names) if not n.startswith(".")
                              and detect.detect(os.path.join(rootdir, n))]
            elif os.path.isfile(p):
                files.append(p)
        for f in files:
            if os.path.realpath(f).startswith(out + os.sep):
                continue  # zaten temizlenmiş kopyalar
            self._add(f)

    def _add(self, path: str) -> None:
        cid = next(self._ids)
        job = Job(path)
        card = ResultCard(self, job)
        self.cards[cid] = card
        self.cards_lay.insertWidget(0, card)
        self.scroll.verticalScrollBar().setValue(0)
        self._update_header()
        self.pool.start(_Task(cid, job, self.signals))

    def _on_done(self, cid: int, dest, error) -> None:
        card = self.cards.get(cid)
        if card:
            r = card.job.report
            log.info("bitti: %s -> %s hata=%s rapor=%s", card.job.path, dest, error, r.error)
            for g in r.gates:
                if not g.passed:
                    log.info("  başarısız kapı %s: %s", g.name, g.details)
            if r.warnings:
                log.info("  uyarılar: %s", r.warnings)
            card.finish(dest, error)
        self._update_header()

    def _update_header(self) -> None:
        total = len(self.cards)
        ready = sum(1 for c in self.cards.values() if c.dest)
        busy = sum(1 for c in self.cards.values() if not c.done)
        self.list_title.setText("" if not total else
                                f"{ready} dosya paylaşıma hazır" + (f" · {busy} işleniyor" if busy else ""))
        for w in (self.btn_clear, self.list_title, self.scroll):
            w.setVisible(total > 0)
        self.steps.setVisible(total == 0)
        self.spacer.setVisible(total == 0)

    def open_output(self) -> None:
        folder = output_dir()
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def clear_list(self) -> None:
        for cid, card in list(self.cards.items()):
            if not card.done:
                continue
            card.setParent(None)
            card.deleteLater()
            del self.cards[cid]
        self._update_header()

    def open_advanced(self) -> None:
        from .main_window import MainWindow
        if self.advanced is None:
            self.advanced = MainWindow()
        self.advanced.show()
        self.advanced.raise_()

    def closeEvent(self, event) -> None:
        for c in self.cards.values():
            c.job.cancel.set()
        self.pool.waitForDone(10000)
        if self.advanced is not None:
            self.advanced.close()
        event.accept()
