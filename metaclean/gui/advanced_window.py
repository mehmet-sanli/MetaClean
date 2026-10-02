"""Gelişmiş mod: dosya listesi, ayrıntı paneli, orijinalin üzerine yazma ve toplu işlem."""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass, field
from itertools import count
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QStyle,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolBar, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import __version__
from ..core import detect, tools
from ..core.handlers.base import Options
from ..core.session import Job, suggest_copy_name
from . import dialogs
from .metaview import MetadataView

log = logging.getLogger("metaclean.gui")

DROP_STYLE = ("QLabel {{ border: 2px dashed {color}; border-radius: 10px; padding: 24px;"
              " color: palette(placeholder-text); font-size: 14px; }}")

GREEN, RED, AMBER, GREY = QColor("#2e9e5b"), QColor("#d64545"), QColor("#c98a00"), QColor("#8a8a8a")

STATES = {
    "queued": ("Sırada", GREY),
    "running": ("İşleniyor…", AMBER),
    "ready": ("Onay bekliyor", GREEN),
    "failed": ("Başarısız", RED),
    "cancelled": ("İptal edildi", GREY),
    "saving": ("Kaydediliyor…", AMBER),
    "saved": ("Kaydedildi", GREEN),
    "copied": ("Kopya kaydedildi", GREEN),
    "discarded": ("Vazgeçildi", GREY),
}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


@dataclass
class Entry:
    id: int
    job: Job
    item: QTreeWidgetItem
    state: str = "queued"
    progress: str = ""
    save_notes: List[str] = field(default_factory=list)
    save_target: str = ""
    save_error: str = ""


class Signals(QObject):
    progress = Signal(int, str)
    done = Signal(int, str, object, object)  # entry id, işlem, sonuç, hata


class Task(QRunnable):
    def __init__(self, eid: int, kind: str, fn: Callable, signals: Signals):
        super().__init__()
        self.eid, self.kind, self.fn, self.signals = eid, kind, fn, signals

    def run(self) -> None:
        try:
            result = self.fn(lambda text: self.signals.progress.emit(self.eid, text))
            self.signals.done.emit(self.eid, self.kind, result, None)
        except Exception as e:  # noqa: BLE001 - hata arayüzde gösterilir
            self.signals.done.emit(self.eid, self.kind, None, e)


class AdvancedWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MetaClean {__version__} – Gelişmiş mod")
        self.resize(1180, 720)
        self.setAcceptDrops(True)
        dialogs.apply_tool_overrides()
        self.entries: Dict[int, Entry] = {}
        self._ids = count(1)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(dialogs.settings().value("jobs/parallel", 2, int))
        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.done.connect(self._on_done)
        self._build_ui()
        self._refresh_actions()
        self._check_tools()

    # ------------------------------------------------------------ arayüz
    def _build_ui(self) -> None:
        st = self.style()
        tb = QToolBar("Araç çubuğu")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.act_add = QAction(st.standardIcon(QStyle.SP_FileIcon), "Dosya ekle…", self)
        self.act_add.setShortcut(QKeySequence.Open)
        self.act_add.triggered.connect(self.add_files_dialog)
        self.act_add_dir = QAction(st.standardIcon(QStyle.SP_DirIcon), "Klasör ekle…", self)
        self.act_add_dir.triggered.connect(self.add_folder_dialog)
        self.act_retry = QAction(st.standardIcon(QStyle.SP_BrowserReload), "Yeniden incele", self)
        self.act_retry.triggered.connect(self.retry_selected)
        self.act_remove = QAction(st.standardIcon(QStyle.SP_TrashIcon), "Listeden kaldır", self)
        self.act_remove.setShortcut(QKeySequence.Delete)
        self.act_remove.triggered.connect(self.remove_selected)
        self.act_save_all = QAction(st.standardIcon(QStyle.SP_DialogSaveButton), "Hazır olanların hepsini kaydet", self)
        self.act_save_all.triggered.connect(self.save_all_ready)
        self.act_settings = QAction(st.standardIcon(QStyle.SP_FileDialogDetailedView), "Ayarlar", self)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_tools = QAction(st.standardIcon(QStyle.SP_ComputerIcon), "Araçlar", self)
        self.act_tools.triggered.connect(lambda: dialogs.InfoDialog("Araçlar", dialogs.tools_html(), self).exec())
        self.act_limits = QAction(st.standardIcon(QStyle.SP_MessageBoxInformation), "Nasıl çalışır / Sınırlar", self)
        self.act_limits.triggered.connect(lambda: dialogs.InfoDialog("Nasıl çalışır", dialogs.LIMITS_HTML, self).exec())
        for a in (self.act_add, self.act_add_dir, None, self.act_retry, self.act_remove, None, self.act_save_all,
                  None, self.act_settings, self.act_tools, self.act_limits):
            tb.addSeparator() if a is None else tb.addAction(a)

        # Sol: dosya listesi (boşken bırakma alanı)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dosya", "Biçim", "Boyut", "Durum"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        drop = QLabel("Dosyaları ya da klasörleri buraya sürükleyin\nveya “Dosya ekle…” ile seçin.\n\n"
                      "JPEG · PNG · WebP · HEIC · AVIF · MP3 · FLAC · M4A · MP4 · MOV · MKV · WebM")
        drop.setAlignment(Qt.AlignCenter)
        drop.setWordWrap(True)
        drop.setStyleSheet(DROP_STYLE.format(color="palette(mid)"))
        self.drop_label = drop
        self.left = QStackedWidget()
        self.left.addWidget(drop)
        self.left.addWidget(self.tree)

        # Sağ: ayrıntılar + onay düğmeleri
        self.tabs = QTabWidget()
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(False)
        self.meta = MetadataView()
        self.changes = QTextBrowser()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.summary, "Özet")
        self.tabs.addTab(self.meta, "Meta veri")
        self.tabs.addTab(self.changes, "Silinen / Korunan")
        self.tabs.addTab(self.log, "Günlük")

        self.btn_save = QPushButton(self.style().standardIcon(QStyle.SP_DialogSaveButton), "Kaydet (üzerine yaz)")
        self.btn_copy = QPushButton(self.style().standardIcon(QStyle.SP_DialogOpenButton), "Kopya olarak kaydet…")
        self.btn_cancel = QPushButton(self.style().standardIcon(QStyle.SP_DialogCancelButton), "İptal")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.save_selected)
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.btn_cancel, self.btn_copy, self.btn_save):
            buttons.addWidget(b)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.tabs, 1)
        rl.addLayout(buttons)

        split = QSplitter()
        split.addWidget(self.left)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([460, 720])
        central = QWidget()
        cl = QVBoxLayout(central)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.banner.setStyleSheet(f"QLabel {{ background: {AMBER.name()}22; border: 1px solid {AMBER.name()};"
                                  " border-radius: 6px; padding: 8px; }")
        self.banner.hide()
        cl.addWidget(self.banner)
        cl.addWidget(split, 1)
        self.setCentralWidget(central)
        # Salt okunur metin alanları bırakılan dosyayı kendileri yutmasın; olay pencereye çıksın
        for wdg in (self.summary, self.changes, self.log, self.meta.tree, self.tree):
            wdg.setAcceptDrops(False)
            wdg.viewport().setAcceptDrops(False)
        self.statusBar().showMessage("Hazır – dosyaları pencerenin herhangi bir yerine sürükleyebilirsiniz")
        self._show_entry(None)

    def _check_tools(self) -> None:
        missing = [t for t in tools.TOOLS if not tools.find_tool(t)]
        if missing:
            import sys
            hint = dialogs.INSTALL_HINTS.get(sys.platform if sys.platform in dialogs.INSTALL_HINTS else "linux")
            self.banner.setText(f"<b>Eksik araç: {', '.join(missing)}.</b> Kurulum: <code>{hint}</code> — "
                                "ya da Ayarlar'dan yolunu gösterin. ExifTool yoksa hiçbir dosya işlenemez; "
                                "FFmpeg yoksa ses ve video işlenemez.")
            self.banner.show()
        else:
            self.banner.hide()

    # ------------------------------------------------------------ dosya ekleme
    # Bazı sistemler (ör. macOS) dragMove kabul edilmezse bırakmaya izin vermez
    def _has_files(self, e) -> bool:
        return e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls())

    def dragEnterEvent(self, e):
        log.info("dragEnter: formatlar=%s urls=%s", e.mimeData().formats(), [u.toString() for u in e.mimeData().urls()])
        if self._has_files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
            self._set_drag_highlight(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._has_files(e):
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._set_drag_highlight(False)

    def dropEvent(self, e):
        log.info("drop: urls=%s", [u.toString() for u in e.mimeData().urls()])
        self._set_drag_highlight(False)
        if not self._has_files(e):
            e.ignore()
            return
        e.setDropAction(Qt.CopyAction)
        e.accept()
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _set_drag_highlight(self, on: bool) -> None:
        color = GREEN.name() if on else "palette(mid)"
        self.drop_label.setStyleSheet(DROP_STYLE.format(color=color))
        self.tree.setStyleSheet(f"QTreeWidget {{ border: 2px solid {GREEN.name()}; }}" if on else "")

    def add_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Temizlenecek dosyalar", "",
            "Medya (*.jpg *.jpeg *.png *.webp *.heic *.heif *.avif *.mp3 *.flac *.m4a *.mp4 *.mov *.mkv *.webm);;"
            "Tüm dosyalar (*)")
        log.info("dosya penceresi döndü: %s", paths)
        self.add_paths(paths)

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Klasör seç")
        if d:
            self.add_paths([d])

    def add_paths(self, paths: List[str]) -> None:
        log.info("add_paths: %s", paths)
        files, skipped = [], 0
        problems: List[str] = []
        for p in paths:
            if os.path.isdir(p):
                for root, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for n in sorted(names):
                        fp = os.path.join(root, n)
                        try:
                            ok = not n.startswith(".") and detect.detect(fp) is not None
                        except OSError:
                            ok = False
                        if ok:
                            files.append(fp)
                        else:
                            skipped += 1
            elif os.path.isfile(p):
                files.append(p)
            else:
                problems.append(f"{p}: dosya bulunamadı ya da okunamıyor")
        active = {os.path.realpath(e.job.path) for e in self.entries.values()
                  if e.state in ("queued", "running", "ready", "saving")}
        added = 0
        for fp in files:
            if os.path.realpath(fp) in active:
                problems.append(f"{os.path.basename(fp)}: zaten listede")
                continue
            active.add(os.path.realpath(fp))
            self._add_entry(fp)
            added += 1
        msg = f"{added} dosya eklendi"
        if skipped:
            msg += f"; klasörlerdeki {skipped} desteklenmeyen dosya atlandı"
        log.info("eklendi=%d atlandı=%d sorunlar=%s", added, skipped, problems)
        self.statusBar().showMessage(msg, 6000)
        if added:
            self.left.setCurrentWidget(self.tree)
        elif paths:
            detail = "\n".join(problems[:10]) or "Seçilen yerde desteklenen medya dosyası bulunamadı."
            QMessageBox.warning(self, "Dosya eklenemedi", detail)

    def _add_entry(self, path: str) -> None:
        eid = next(self._ids)
        item = QTreeWidgetItem([os.path.basename(path), "", "", ""])
        item.setToolTip(0, path)
        item.setData(0, Qt.UserRole, eid)
        try:
            item.setText(2, human_size(os.path.getsize(path)))
            fmt = detect.detect(os.path.realpath(path))
            item.setText(1, detect.label(fmt) if fmt else "?")
        except OSError:
            pass
        self.tree.addTopLevelItem(item)
        entry = Entry(eid, Job(path, self._options()), item)
        self.entries[eid] = entry
        if not self.tree.selectedItems():
            item.setSelected(True)
        self._start_prepare(entry)

    def _options(self) -> Options:
        return Options(full_video_decode=dialogs.settings().value("verify/full", False, bool))

    # ------------------------------------------------------------ işler
    def _start_prepare(self, entry: Entry) -> None:
        self._set_state(entry, "queued")
        self.pool.start(Task(entry.id, "prepare", lambda progress: entry.job.prepare(progress), self.signals))

    def _on_progress(self, eid: int, text: str) -> None:
        e = self.entries.get(eid)
        if not e:
            return
        if e.state == "queued":
            self._set_state(e, "running")
        e.progress = text
        e.item.setText(3, f"{STATES[e.state][0]} {text}")
        if self._current() is e:
            self.statusBar().showMessage(f"{os.path.basename(e.job.path)}: {text}")

    def _on_done(self, eid: int, kind: str, result, error) -> None:
        e = self.entries.get(eid)
        if e:
            log.info("bitti: %s %s hata=%s rapor_hatası=%s", kind, e.job.path, error, e.job.report.error)
        if not e:
            return
        if kind == "prepare":
            r = e.job.report
            if error is not None:
                r.error = str(error)
            self._set_state(e, "ready" if e.job.ready else ("cancelled" if r.error == "İptal edildi." else "failed"))
        else:
            if error is not None:
                e.save_error = str(error)
                self._set_state(e, "failed")
                QMessageBox.critical(self, "Kaydedilemedi", f"{os.path.basename(e.job.path)}:\n{error}")
            else:
                e.save_notes = result
                self._set_state(e, "saved" if kind == "save" else "copied")
        if self._current() is e:
            self._show_entry(e)
        self._refresh_actions()
        busy = sum(1 for x in self.entries.values() if x.state in ("queued", "running", "saving"))
        ready = sum(1 for x in self.entries.values() if x.state == "ready")
        self.statusBar().showMessage(f"{busy} dosya işleniyor · {ready} dosya onay bekliyor" if busy or ready
                                     else "Tüm işler bitti")

    def _set_state(self, e: Entry, state: str) -> None:
        e.state = state
        text, color = STATES[state]
        e.item.setText(3, text)
        e.item.setForeground(3, QBrush(color))

    # ------------------------------------------------------------ seçim ve ayrıntılar
    def _selected(self) -> List[Entry]:
        return [self.entries[i.data(0, Qt.UserRole)] for i in self.tree.selectedItems()]

    def _current(self) -> Optional[Entry]:
        sel = self._selected()
        return sel[0] if len(sel) == 1 else None

    def _on_selection(self) -> None:
        self._show_entry(self._current())
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        sel = self._selected()
        one = self._current()
        self.btn_save.setEnabled(bool(sel) and all(e.state == "ready" for e in sel))
        self.btn_copy.setEnabled(one is not None and one.state == "ready")
        self.btn_cancel.setEnabled(any(e.state in ("queued", "running", "ready") for e in sel))
        self.btn_cancel.setText("Vazgeç" if sel and all(e.state == "ready" for e in sel) else "İptal")
        self.act_retry.setEnabled(any(e.state in ("failed", "cancelled", "discarded") for e in sel))
        self.act_remove.setEnabled(any(e.state not in ("running", "saving") for e in sel))
        self.act_save_all.setEnabled(any(e.state == "ready" for e in self.entries.values()))

    def _show_entry(self, e: Optional[Entry]) -> None:
        if e is None:
            n = len(self._selected())
            self.summary.setHtml(f"<p style='color:gray'>{f'{n} dosya seçili.' if n > 1 else 'Ayrıntılar için bir dosya seçin.'}</p>")
            self.meta.set_scans({}, None)
            self.tabs.setTabText(1, "Meta veri")
            self.changes.clear()
            self.log.clear()
            return
        r = e.job.report
        esc = html.escape
        parts = [f"<h3 style='margin-bottom:2px'>{esc(os.path.basename(e.job.path))}</h3>",
                 f"<p style='color:gray;margin-top:0'>{esc(e.job.real)}"
                 f"{' · ' + esc(detect.label(r.fmt)) if r.fmt else ''}</p>"]
        text, color = STATES[e.state]
        parts.append(f"<p><b style='color:{color.name()}'>● {esc(text)}</b> {esc(e.progress) if e.state == 'running' else ''}</p>")
        if r.error:
            parts.append(f"<p style='color:{RED.name()}'><b>Hata:</b> {esc(r.error)}</p>")
        if e.save_error:
            parts.append(f"<p style='color:{RED.name()}'><b>Kaydetme hatası:</b> {esc(e.save_error)}</p>")
        if e.state in ("saved", "copied"):
            target = e.save_target or e.job.real
            parts.append(f"<p><b>Yazıldı:</b> {esc(target)}</p><p><b>Zaman damgaları (geri okundu):</b><br>"
                         + "<br>".join(esc(n) for n in e.save_notes) + "</p>")
        if r.gates:
            parts.append("<h4>Doğrulama kapıları</h4>")
            for g in r.gates:
                c = GREEN if g.passed else RED
                parts.append(f"<p style='margin-bottom:0'><b style='color:{c.name()}'>{'✓' if g.passed else '✗'} {esc(g.name)}</b></p>"
                             "<ul style='margin-top:0'>" + "".join(f"<li>{esc(d)}</li>" for d in g.details) + "</ul>")
            if e.state == "ready":
                parts.append(f"<p><b>Orijinalde {len(r.found)} hassas alan bulundu, {len(r.removed)} yapı silindi, "
                             f"{len(r.kept)} öğe bilerek korundu.</b> Kaydet, Kopya olarak kaydet ya da İptal'i seçin.</p>")
        for w in r.warnings:
            parts.append(f"<p style='color:{AMBER.name()}'>⚠ {esc(w)}</p>")
        self.summary.setHtml("".join(parts))

        self.meta.set_scans(r.scan, r.clean_scan if e.state in ("ready", "saved", "copied") else None,
                            cleanable=bool(r.fmt))
        self.tabs.setTabText(1, f"Meta veri ({self.meta.count_text()})")

        ch = ["<h4>Silinen</h4><ul>"] + [f"<li>{esc(x)}</li>" for x in r.removed] + ["</ul>"]
        ch += ["<h4>Bilerek korunan</h4><ul>"] + [f"<li><b>{esc(k.what)}</b> — {esc(k.reason)}</li>" for k in r.kept] + ["</ul>"]
        if r.remaining:
            ch += [f"<h4 style='color:{RED.name()}'>Yeniden taramada kalan</h4><ul>"]
            ch += [f"<li>{esc(f.key)} = {esc(f.value)}</li>" for f in r.remaining] + ["</ul>"]
        self.changes.setHtml("".join(ch))
        self.log.setPlainText("\n".join(r.log))

    # ------------------------------------------------------------ onay eylemleri
    def _when(self) -> Optional[float]:
        s = dialogs.settings()
        return float(s.value("ts/fixed", 0, int)) if s.value("ts/mode", "now") == "fixed" else None

    def _run_save(self, e: Entry, kind: str, fn: Callable[[], List[str]]) -> None:
        self._set_state(e, "saving")
        self._refresh_actions()
        self.pool.start(Task(e.id, kind, lambda progress: fn(), self.signals))

    def save_selected(self) -> None:
        sel = [e for e in self._selected() if e.state == "ready"]
        if sel:
            self._confirm_and_save(sel)

    def save_all_ready(self) -> None:
        ready = [e for e in self.entries.values() if e.state == "ready"]
        if ready:
            self._confirm_and_save(ready)

    def _confirm_and_save(self, entries: List[Entry]) -> None:
        names = "\n".join("• " + os.path.basename(e.job.path) for e in entries[:12])
        if len(entries) > 12:
            names += f"\n… ve {len(entries) - 12} dosya daha"
        warns = [w for e in entries for w in e.job.report.warnings if "sabit bağ" in w.lower()]
        text = (f"{len(entries)} dosyanın üzerine doğrulanmış temiz sürümü yazılacak:\n\n{names}\n\n"
                "Bu işlem geri alınamaz. Eski içerik diskte bir süre kurtarılabilir kalabilir; bulut sürüm "
                "geçmişi ve yedekler programın erişimi dışındadır.")
        if warns:
            text += "\n\n⚠ " + warns[0]
        box = QMessageBox(QMessageBox.Warning, "Üzerine yazılsın mı?", text, parent=self)
        ok = box.addButton("Kaydet", QMessageBox.AcceptRole)
        box.addButton("Vazgeç", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            return
        when = self._when()
        for e in entries:
            self._run_save(e, "save", lambda job=e.job: job.save_replace(when))

    def copy_selected(self) -> None:
        e = self._current()
        if not e or e.state != "ready":
            return
        ext = detect.ext(e.job.report.fmt)
        dest, _ = QFileDialog.getSaveFileName(self, "Temiz kopyayı kaydet", suggest_copy_name(e.job.real, e.job.report.fmt),
                                              f"{detect.label(e.job.report.fmt)} (*{ext})")
        if not dest:
            return
        if not os.path.splitext(dest)[1]:
            dest += ext
        if os.path.realpath(dest) == e.job.real:
            QMessageBox.warning(self, "Kopya", "Kopya orijinalin üzerine yazılamaz; bunun için 'Kaydet'i kullanın.")
            return
        e.save_target = dest
        when = self._when()
        self._run_save(e, "copy", lambda: e.job.save_copy(dest, when))

    def cancel_selected(self) -> None:
        for e in self._selected():
            if e.state in ("queued", "running"):
                e.job.cancel.set()
                e.item.setText(3, "İptal ediliyor…")
            elif e.state == "ready":
                e.job.discard()
                self._set_state(e, "discarded")
        self._on_selection()

    def retry_selected(self) -> None:
        for e in self._selected():
            if e.state in ("failed", "cancelled", "discarded"):
                e.job = Job(e.job.path, self._options())
                e.save_notes, e.save_error, e.save_target = [], "", ""
                self._start_prepare(e)
        self._on_selection()

    def remove_selected(self) -> None:
        for e in self._selected():
            if e.state in ("running", "saving"):
                continue
            if e.state == "queued":
                e.job.cancel.set()
            e.job.discard()
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(e.item))
            del self.entries[e.id]
        if not self.entries:
            self.left.setCurrentIndex(0)
        self._on_selection()

    def open_settings(self) -> None:
        if dialogs.SettingsDialog(self).exec():
            self.pool.setMaxThreadCount(dialogs.settings().value("jobs/parallel", 2, int))
            self._check_tools()

    # ------------------------------------------------------------ kapanış
    def closeEvent(self, event) -> None:
        busy = [e for e in self.entries.values() if e.state in ("queued", "running", "saving")]
        ready = [e for e in self.entries.values() if e.state == "ready"]
        if busy or ready:
            ans = QMessageBox.question(self, "Çıkılsın mı?",
                                       f"{len(busy)} dosya işleniyor, {len(ready)} dosya onay bekliyor. "
                                       "Çıkarsanız onay bekleyen temiz sürümler silinir; orijinallere dokunulmaz.")
            if ans != QMessageBox.Yes:
                event.ignore()
                return
        for e in self.entries.values():
            e.job.cancel.set()
        self.pool.waitForDone(10000)
        for e in self.entries.values():
            if e.state != "saving":
                e.job.discard()
        event.accept()
