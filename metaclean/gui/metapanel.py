"""Ana ekranın altındaki meta veri paneli: seçili dosyada hangi gizli bilgilerin olduğunu sade bir dille gösterir."""
from __future__ import annotations

import html
from typing import Dict, Optional

from PySide6.QtWidgets import QFrame, QLabel, QTextBrowser, QVBoxLayout

from ..core import categories

READING, FOUND, READY, CLEANED, DISCARDED, FAILED = ("okunuyor", "bulundu", "onay bekliyor", "temizlendi",
                                                     "vazgeçildi", "temizlenemedi")

_BADGE = {
    # durum -> (satırdaki silinecek alanın yazısı, rengi)
    READING: ("Silinecek", "#d64545"),
    FOUND: ("Silinecek", "#d64545"),
    READY: ("Kaydedince silinecek", "#d64545"),
    DISCARDED: ("Kaydedilmedi", "#8a8a8a"),
    CLEANED: ("Silindi ✓", "#2e9e5b"),
    FAILED: ("Dosyada kaldı", "#c98a00"),
}


class MetaPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("metaPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("metaTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("metaSub")
        self.subtitle.setWordWrap(True)
        self.body = QTextBrowser()
        self.body.setOpenLinks(False)  # değerler dosyadan gelir; bağlantı açılmasın
        self.body.setOpenExternalLinks(False)
        self.body.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        lay.addWidget(self.body, 1)
        self.sections = []

    def show_scan(self, name: str, scan: Optional[Dict[str, object]], state: str) -> None:
        self.title.setText(f"📋 Meta veri — {html.escape(name)}")
        if state == READING and not scan:
            self.subtitle.setText("Dosyanın içindeki bilgiler okunuyor…")
            self.body.setHtml("")
            self.sections = []
            return
        self.sections = categories.readable(scan or {})
        personal = sum(len(rows) for _, title, rows in self.sections if not title.startswith("Korunan"))
        if not scan:
            self.subtitle.setText("Bu dosyanın meta verisi okunamadı.")
        elif personal == 0:
            self.subtitle.setText("Bu dosyada kişisel bilgi bulunmadı.")
        elif state == CLEANED:
            self.subtitle.setText(f"Orijinalde {personal} bilgi vardı; kaydedilen temiz kopyada hepsi silindi.")
        elif state == READY:
            self.subtitle.setText(f"{personal} bilgi bulundu. Temiz kopya hazır ve doğrulandı; “Kaydet”e basınca "
                                  "bu bilgiler olmadan kaydedilir.")
        elif state == DISCARDED:
            self.subtitle.setText("Vazgeçildi; hiçbir dosya kaydedilmedi. Orijinal dosya olduğu gibi duruyor.")
        elif state == FAILED:
            self.subtitle.setText(f"{personal} bilgi bulundu, ama bu dosya temizlenemedi; bilgiler dosyada duruyor.")
        else:
            self.subtitle.setText(f"{personal} kişisel ya da teknik bilgi bulundu; temiz kopyada silinecek.")
        self.body.setHtml(self._html(state))

    def _html(self, state: str) -> str:
        removed_text, removed_color = _BADGE[state]
        parts = []
        for icon, title, rows in self.sections:
            parts.append(f"<p style='margin:8px 0 2px 0'><b>{icon} {html.escape(title)}</b></p>"
                         "<table width='100%' cellspacing='0' cellpadding='3'>")
            for label, value, status in rows:
                if status == categories.STATUS_KEPT:
                    badge, color = "Korunuyor", "#2e9e5b"
                else:
                    badge, color = removed_text, removed_color
                parts.append(f"<tr><td width='38%' style='color:gray'>{html.escape(label)}</td>"
                             f"<td>{html.escape(value)}</td>"
                             f"<td align='right' style='color:{color}; white-space:nowrap'>{badge}</td></tr>")
            parts.append("</table>")
        return "".join(parts)
