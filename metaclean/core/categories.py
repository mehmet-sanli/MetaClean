"""Teknik alan adlarını herkesin anlayacağı başlıklara çevirir (📍 Konum, 📷 Cihaz…)."""
from __future__ import annotations

from typing import Dict, List, Tuple

from .report import Report

# (anahtar, simge, başlık) — gösterim sırası
CATEGORIES = [
    ("konum", "📍", "Konum"),
    ("kisi", "👤", "İsim / sahip"),
    ("cihaz", "📷", "Cihaz bilgisi"),
    ("tarih", "🕒", "Tarih ve saat"),
    ("aciklama", "📝", "Açıklama ve etiketler"),
    ("resim", "🖼", "Küçük resim / kapak"),
    ("ekveri", "🎞", "Gizli ek veri"),
    ("gecmis", "🛠", "Düzenleme geçmişi"),
    ("diger", "•", "Diğer teknik bilgiler"),
]

_RULES = [
    ("gecmis", ("history", "derived", "documentid", "instanceid", "xmptoolkit", "c2pa", "jumbf", "creatortool")),
    ("konum", ("gps", "location", "xyz", "city", "country", "state", "sublocation", "latitude", "longitude")),
    ("kisi", ("artist", "author", "creator", "by-line", "copyright", "owner", "rights", "writer", "credit",
              "contact", "composer", "publisher", "albumartist")),
    ("cihaz", ("make", "model", "serial", "lens", "software", "compressorname", "encoder", "hostcomputer",
               "firmware", "cameraid", "uniqueid", "bodyserial", "vendor")),
    ("resim", ("thumbnail", "preview", "picture", "coverart", "cover")),
    ("tarih", ("date", "time", "year")),
    ("aciklama", ("title", "comment", "keyword", "description", "caption", "subject", "album", "genre", "label",
                  "headline", "lyrics", "rating", "category", "chapter")),
]


def categorize(group: str, name: str) -> str:
    g, n = group.lower(), name.lower()
    if g == "gps":
        return "konum"
    if g in ("makernotes", "apple") or g.startswith("makernotes"):
        return "cihaz"
    if g in ("photoshop",) or g.startswith("xmp-xmpmm"):
        return "gecmis"
    for cat, words in _RULES:
        if any(w in n for w in words):
            return cat
    return "diger"


def _removed_category(text: str) -> str:
    t = text.lower()
    # EXIF bloğunun genel açıklamasında da "küçük resim" geçer; yalnızca gerçekten silinen resimleri say
    if "kapak resmi" in t or "küçük resmi" in t:
        return "resim"
    if "sonrası ek veri" in t:
        return "ekveri"
    if "telemetri" in t or "gps" in t:
        return "konum"
    if "zaman kodu" in t:
        return "tarih"
    if "bölüm" in t:
        return "aciklama"
    if "c2pa" in t:
        return "gecmis"
    return ""


def summarize(report: Report) -> List[Tuple[str, str, str]]:
    """[(simge, başlık, örnek değer)] — yalnızca gerçekten bulunan kategoriler."""
    found: Dict[str, str] = {}
    gps = report.scan.get("Composite:GPSPosition")
    for f in report.found:
        cat = categorize(f.group, f.name)
        if cat not in found or (not found[cat] and f.value):
            value = f.value
            if cat == "konum" and gps:
                value = str(gps)
            if cat == "diger" or value.startswith("(Binary") or len(value) > 60 or not any(c.isalpha() for c in value):
                value = ""  # yalnızca sayı ya da ikili veri: kullanıcıya bir şey anlatmaz
            value = value.replace(" deg ", "°").replace("' ", "'")
            found[cat] = value
    for text in report.removed:
        cat = _removed_category(text)
        if cat and cat not in found:
            found[cat] = ""
    return [(icon, title, found[key]) for key, icon, title in CATEGORIES if key in found]
