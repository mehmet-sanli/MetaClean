"""Meta veri görüntüleyici: ExifTool taramasını gruplar hâlinde, durumuyla birlikte gösterir."""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import allowlist
from ..i18n import source, tr

RED, GREEN, GREY, AMBER = QColor("#d64545"), QColor("#2e9e5b"), QColor("#8a8a8a"), QColor("#c98a00")

# Bilerek korunan (yük taşıyan) alanlar; geri kalan izinli alanlar "yapısal" sayılır
KEPT_NAMES = {"Orientation", "iTunSMPB", "WaveformatextensibleChannelMask", "ColorTransform", "Rotation",
              "MatrixStructure", "ColorPrimaries", "TransferCharacteristics", "MatrixCoefficients",
              "TrackLanguage", "MediaLanguageCode", "AttachedFileName"}

GROUP_TITLES = {
    "IFD0": "EXIF – ana görüntü", "ExifIFD": "EXIF – çekim bilgileri", "GPS": "GPS konumu",
    "IFD1": "EXIF – küçük resim", "InteropIFD": "EXIF – uyumluluk", "MakerNotes": "Üretici notları",
    "XMP-x": "XMP", "IPTC": "IPTC", "Photoshop": "Photoshop", "ICC_Profile": "ICC renk profili",
    "ICC-header": "ICC profil başlığı", "JFIF": "JFIF", "File": "Dosya", "System": "Dosya sistemi",
    "Composite": "Türetilmiş (ExifTool hesapladı)", "QuickTime": "QuickTime / MP4", "Matroska": "Matroska",
    "Info": "Matroska bilgisi", "ID3v2_3": "ID3v2.3 etiketleri", "ID3v2_4": "ID3v2.4 etiketleri",
    "ID3v1": "ID3v1 etiketi", "APE": "APE etiketi", "Vorbis": "Vorbis yorumları", "FLAC": "FLAC akış bilgisi",
    "MPEG": "MPEG ses başlığı", "PNG": "PNG", "RIFF": "RIFF / WebP", "iTunes": "iTunes etiketleri",
    "ItemList": "iTunes etiketleri", "Keys": "Apple anahtarları", "UserData": "Kullanıcı verisi (udta)",
}


def classify(group: str, name: str, value: object) -> tuple:
    """(etiket, renk, hassas mı)"""
    if group == "System" or (group == "File" and name != "Comment"):
        return tr("Dosya sistemi"), GREY, False
    if group == "ExifTool":
        return tr("Araç"), GREY, False
    if group == "Composite":
        sensitive = any(k in name for k in ("GPS", "Date", "Serial", "Lens"))
        return (tr("Türetilmiş – kaynağıyla silinir") if sensitive else tr("Türetilmiş")), (RED if sensitive else GREY), False
    if not allowlist.is_allowed(group, name, value):
        return tr("Hassas – silinir"), RED, True
    if group.startswith("ICC") or name in KEPT_NAMES or group == "Adobe":
        return tr("Korunur (görüntüleme için)"), GREEN, False
    return tr("Yapısal"), GREY, False


class MetadataView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scans: Dict[str, Dict[str, object]] = {}
        self._cleanable = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._render)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Ara: alan, değer ya da grup (ör. GPS, Date, Model)"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        self.only_sensitive = QCheckBox(tr("Yalnızca hassas"))
        self.only_sensitive.toggled.connect(self._render)
        self.hide_fs = QCheckBox(tr("Dosya sistemini gizle"))
        self.hide_fs.setChecked(True)
        self.hide_fs.toggled.connect(self._render)
        copy = QPushButton(tr("Kopyala"))
        copy.setToolTip(tr("Görünen alanları metin olarak panoya kopyalar"))
        copy.clicked.connect(self._copy)
        bar.addWidget(self.source)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.only_sensitive)
        bar.addWidget(self.hide_fs)
        bar.addWidget(copy)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Alan"), tr("Değer"), tr("Durum")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(0, 230)
        lay.addWidget(self.tree, 1)
        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.summary)

    def set_scans(self, original: Dict[str, object], clean: Optional[Dict[str, object]],
                  cleanable: bool = True) -> None:
        self._cleanable = cleanable
        self._scans = {}
        if original:
            self._scans[tr("Orijinal dosya")] = original
        if clean:
            self._scans[tr("Temiz sürüm (kaydedilecek)")] = clean
        keep = self.source.currentText()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItems(list(self._scans))
        if keep in self._scans:
            self.source.setCurrentText(keep)
        self.source.setVisible(len(self._scans) > 1)
        self.source.blockSignals(False)
        self._render()

    def count_text(self) -> str:
        scan = self._scans.get(tr("Orijinal dosya"), {})
        fields = [k for k in scan if ":" in k]
        hassas = sum(1 for k in fields if classify(*k.split(":", 1), scan[k])[2])
        return tr("{s} hassas / {n} alan", s=hassas, n=len(fields)) if fields else "—"

    def _render(self) -> None:
        self.tree.clear()
        scan = self._scans.get(self.source.currentText(), {})
        needle = self.search.text().strip().lower()
        groups: Dict[str, QTreeWidgetItem] = {}
        shown = total = hassas = 0
        for key, value in scan.items():
            if ":" not in key:
                continue
            group, name = key.split(":", 1)
            label, color, sensitive = classify(group, name, value)
            if not self._cleanable and source(label) not in ("Dosya sistemi", "Araç"):
                # Bu biçim temizlenemiyor: hiçbir alan silinmeyecek, yalnızca okunuyor
                label, color = (tr("Kişisel olabilir – bu biçim temizlenemez"), AMBER) if sensitive else (tr("Okundu"), GREY)
            total += 1
            hassas += sensitive
            if self.hide_fs.isChecked() and source(label) in ("Dosya sistemi", "Araç"):
                continue
            if self.only_sensitive.isChecked() and not sensitive:
                continue
            text = str(value)
            if needle and needle not in f"{group} {name} {text}".lower():
                continue
            parent = groups.get(group)
            if parent is None:
                parent = QTreeWidgetItem([tr(GROUP_TITLES[group]) if group in GROUP_TITLES else group, "", ""])
                f = parent.font(0)
                f.setBold(True)
                parent.setFont(0, f)
                parent.setFirstColumnSpanned(False)
                parent.setData(0, Qt.UserRole, [0, 0])
                groups[group] = parent
                self.tree.addTopLevelItem(parent)
            short = text if len(text) <= 300 else text[:297] + "…"
            it = QTreeWidgetItem([name, short, label])
            it.setToolTip(0, key)
            it.setToolTip(1, text[:2000])
            it.setForeground(2, QBrush(color))
            if sensitive:
                it.setForeground(0, QBrush(RED))
            parent.addChild(it)
            stats = parent.data(0, Qt.UserRole)
            stats[0] += 1
            stats[1] += sensitive
            parent.setData(0, Qt.UserRole, stats)
            shown += 1
        for g, parent in groups.items():
            n, s = parent.data(0, Qt.UserRole)
            parent.setText(2, tr("{n} alan, {s} hassas", n=n, s=s) if s else tr("{n} alan", n=n))
            parent.setForeground(2, QBrush(RED if s else GREY))
            parent.setToolTip(0, g)
        self.tree.expandAll()
        if not scan:
            self.summary.setText(tr("Meta veri okunmadı (ExifTool bulunamadı ya da dosya henüz incelenmedi)."))
        else:
            self.summary.setText(tr("{total} alan, {s} hassas · {shown} gösteriliyor · {g} grup", total=total, s=hassas,
                                    shown=shown, g=len(groups)))

    def _copy(self) -> None:
        lines = []
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            lines.append(f"[{g.toolTip(0)}]")
            for j in range(g.childCount()):
                c = g.child(j)
                lines.append(f"  {c.text(0)} = {c.toolTip(1)}  ({c.text(2)})")
        QGuiApplication.clipboard().setText("\n".join(lines))
