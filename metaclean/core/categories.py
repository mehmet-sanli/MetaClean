"""Teknik alan adlarını herkesin anlayacağı başlıklara çevirir (📍 Konum, 📷 Cihaz…)."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from . import allowlist
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
    # EXIF bloğunun genel açıklamasında da "GPS" geçer; yalnızca gerçek konum izlerini say
    if "telemetri" in t or "gps izi" in t or "uçuş verisi" in t:
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


# ---------------------------------------------------------------- okunur meta veri listesi
# Sık görülen alanların herkesin anlayacağı adları; listede olmayanlar CamelCase'ten bölünür
LABELS = {
    "Make": "Cihaz markası", "Model": "Cihaz modeli", "LensModel": "Lens", "LensMake": "Lens markası",
    "LensInfo": "Lens bilgisi", "Software": "Yazılım", "HostComputer": "Bilgisayar",
    "SerialNumber": "Seri numarası", "BodySerialNumber": "Gövde seri numarası",
    "LensSerialNumber": "Lens seri numarası", "OwnerName": "Sahip", "CameraOwnerName": "Kamera sahibi",
    "DateTimeOriginal": "Çekim tarihi", "CreateDate": "Oluşturma tarihi", "ModifyDate": "Değiştirme tarihi",
    "DateCreated": "Oluşturma tarihi", "MetadataDate": "Meta veri tarihi", "DateTime": "Tarih",
    "OffsetTime": "Saat dilimi", "OffsetTimeOriginal": "Çekim saat dilimi",
    "GPSPosition": "Konum (koordinat)", "GPSLatitude": "Enlem", "GPSLongitude": "Boylam",
    "GPSAltitude": "Yükseklik", "GPSSpeed": "Hız", "GPSImgDirection": "Çekim yönü",
    "GPSDateStamp": "GPS tarihi", "GPSTimeStamp": "GPS saati", "City": "Şehir", "Country": "Ülke",
    "Location": "Yer", "LocationName": "Yer",
    "Artist": "Fotoğrafçı / sanatçı", "Author": "Yazar", "Creator": "Oluşturan", "By-line": "Oluşturan",
    "Copyright": "Telif hakkı", "Rights": "Haklar", "Credit": "Kaynak", "Title": "Başlık",
    "ImageDescription": "Açıklama", "Description": "Açıklama", "Caption-Abstract": "Açıklama",
    "UserComment": "Kullanıcı yorumu", "Comment": "Yorum", "Keywords": "Anahtar kelimeler",
    "Subject": "Konular", "Rating": "Puan", "Album": "Albüm", "Genre": "Tür", "Year": "Yıl",
    "DigitalSourceType": "İçerik kaynağı", "CreatorTool": "Düzenleme programı",
    "XMPToolkit": "XMP aracı", "HistoryAction": "Düzenleme geçmişi",
    "ExposureTime": "Pozlama süresi", "FNumber": "Diyafram", "ISO": "ISO", "FocalLength": "Odak uzaklığı",
    "Flash": "Flaş", "WhiteBalance": "Beyaz dengesi", "ExposureProgram": "Çekim modu",
    "SceneCaptureType": "Sahne türü", "ExifVersion": "EXIF sürümü", "FlashpixVersion": "FlashPix sürümü",
    "ColorSpace": "Renk alanı", "ExifImageWidth": "Kayıtlı genişlik", "ExifImageHeight": "Kayıtlı yükseklik",
    "ComponentsConfiguration": "Renk bileşenleri", "Orientation": "Yön",
    "ProfileDescription": "Renk profili", "ImageSize": "Boyut", "Megapixels": "Megapiksel",
    "FileType": "Dosya türü", "Duration": "Süre", "ThumbnailImage": "Küçük resim",
}

# Bazı değerlerin okunur karşılığı
VALUES = {
    "trainedAlgorithmicMedia": "Yapay zekâ ile üretilmiş",
    "compositeWithTrainedAlgorithmicMedia": "Yapay zekâ ile düzenlenmiş",
    "algorithmicMedia": "Bilgisayarla üretilmiş",
    "digitalCapture": "Kamerayla çekilmiş",
    "Horizontal (normal)": "Normal",
    "Standard": "Standart",
}

_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
           "Kasım", "Aralık")
_DATE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})(?: (\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?")
# İzin listesindeki (silinmeyen) alanlardan yalnızca bunlar gösterilir; geri kalanı iç yapıdır
_SHOWN_KEPT = {"ImageSize", "FileType", "ProfileDescription", "Orientation"}
# Silinse de kullanıcıya bir şey anlatmayan iç yapı alanları
_HIDDEN_NAMES = {"YCbCrPositioning", "XResolution", "YResolution", "ResolutionUnit", "EncodingProcess",
                 "BitsPerSample", "ColorComponents", "YCbCrSubSampling", "ExifByteOrder", "MIMEType",
                 "FileTypeExtension", "ImageWidth", "ImageHeight", "CurrentIPTCDigest", "ProfileID"}

STATUS_REMOVED, STATUS_KEPT = "silinir", "korunur"
COORD_LABEL = "Konum (koordinat)"

# Videolardaki koordinat biçimleri: ISO 6709 (+41.0082+028.9784/) ve 3GPP loci (Lat=41.00819 Lon=28.97839)
_ISO6709 = re.compile(r"^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")
_LATLON = re.compile(r"Lat=(-?\d+(?:\.\d+)?)\s+Lon=(-?\d+(?:\.\d+)?)")
_DMS = re.compile(r"\d+ deg \d+' [\d.]+\"")  # EXIF enlemi yön harfini (N/S) ayrı alanda taşır


def _dms(value: float, pos: str, neg: str) -> str:
    d = abs(value)
    deg = int(d)
    minutes = int((d - deg) * 60)
    seconds = (d - deg - minutes / 60) * 3600
    return f"{deg}°{minutes}'{seconds:.2f}\" {pos if value >= 0 else neg}"


def coordinates(value: object) -> str:
    """Koordinat taşıyan bir değeri fotoğraflardaki biçime çevirir; koordinat değilse boş döner."""
    v = str(value)
    m = _ISO6709.match(v) or _LATLON.search(v)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return f"{_dms(lat, 'N', 'S')}, {_dms(lon, 'E', 'W')}"
    return pretty_value(v) if _DMS.search(v) else ""


def label(name: str) -> str:
    if name in LABELS:
        return LABELS[name]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


def pretty_value(value: object) -> str:
    v = str(value).strip()
    if v.startswith("(Binary") or v.startswith("base64:"):
        return "(gömülü veri)"
    tail = v.rsplit("/", 1)[-1]  # IPTC sözlük adresleri: .../digitalsourcetype/trainedAlgorithmicMedia
    if tail in VALUES:
        return VALUES[tail]
    if v in VALUES:
        return VALUES[v]
    m = _DATE.match(v)
    if m and m.group(1) != "0000":
        y, mo, d, hh, mm = m.groups()
        try:
            text = f"{int(d)} {_MONTHS[int(mo) - 1]} {y}"
        except (IndexError, ValueError):
            return v
        return text + (f", {hh}:{mm}" if hh else "")
    v = v.replace(" deg ", "°").replace("' ", "'")
    return v if len(v) <= 120 else v[:117] + "…"


def readable(scan: Dict[str, object]) -> List[Tuple[str, str, List[Tuple[str, str, str]]]]:
    """Taramayı herkesin anlayacağı bölümlere çevirir:
    [(simge, başlık, [(alan adı, değer, durum)])]. durum: silinir | korunur."""
    rows: Dict[str, List[Tuple[str, str, str]]] = {}
    kept: List[Tuple[str, str, str]] = []
    ai: List[Tuple[str, str, str]] = []
    gps = scan.get("Composite:GPSPosition")
    for key, value in scan.items():
        if ":" not in key:
            continue
        group, name = key.split(":", 1)
        if name in _HIDDEN_NAMES:
            continue
        if allowlist.is_allowed(group, name, value):  # silinmeyenler: dosya sistemi, ICC, yön…
            if name in _SHOWN_KEPT:
                kept.append((label(name), pretty_value(value), STATUS_KEPT))
            continue
        if name == "DigitalSourceType":
            ai.append((label(name), pretty_value(value), STATUS_REMOVED))
            continue
        cat = categorize(group, name)
        if cat == "konum":
            if name.endswith("Ref") or name == "GPSVersionID":
                continue  # yön harfleri (N/E) ve sürüm koordinatın içinde zaten var
            coord = coordinates(value)
            if coord:
                # Aynı konum birden çok alanda olabilir (iPhone videosu: Keys + UserData); tek satır göster
                if not any(n == COORD_LABEL for n, _, _ in rows.get(cat, [])):
                    rows.setdefault(cat, []).insert(0, (COORD_LABEL, pretty_value(gps) if gps else coord,
                                                        STATUS_REMOVED))
                continue
        rows.setdefault(cat, []).append((label(name), pretty_value(value), STATUS_REMOVED))
    out = [("🤖", "İçerik kaynağı", ai)] if ai else []
    out += [(icon, title, rows[k]) for k, icon, title in CATEGORIES if k in rows]
    if kept:
        out.append(("✅", "Korunan (kişisel değil)", kept))
    return out
