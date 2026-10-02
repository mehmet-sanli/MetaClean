"""Görseller: JPEG, PNG, WebP blok düzeyinde (yeniden sıkıştırma yok); HEIC/AVIF ExifTool ile."""
from __future__ import annotations

import hashlib
import struct
import zlib
from typing import Dict, List, Optional, Tuple

from .. import tiffmin, tools
from ..report import Gate
from .base import Context, Handler

ENTROPY = -1

# ---------------------------------------------------------------- Pillow ile çözme

_INFO_KEYS = ("gamma", "chromaticity", "srgb", "transparency", "adobe", "adobe_transform",
              "jfif_unit", "jfif_density", "progressive", "loop", "duration", "disposal", "blend",
              "background")


def _pil():
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # yerel dosya; sıkıştırma bombası uyarısı gereksiz
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image


def decode_digest(path: str) -> Tuple[str, Dict[str, object]]:
    """Tüm kareleri tam çözer (load), ham piksellerin SHA-256'sını ve teknik parametreleri döndürür."""
    Image = _pil()
    h = hashlib.sha256()
    params: Dict[str, object] = {}
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        params["biçim"] = im.format
        params["kare sayısı"] = n
        params["yön"] = im.getexif().get(tiffmin.ORIENTATION) or 1
        icc = im.info.get("icc_profile")
        params["ICC"] = hashlib.sha256(icc).hexdigest()[:16] if icc else None
        for i in range(n):
            im.seek(i)
            im.load()
            h.update(f"{i}|{im.mode}|{im.size}|".encode())
            h.update(im.tobytes())
            params[f"kare {i}"] = (im.mode, im.size,
                                   tuple((k, repr(im.info.get(k))) for k in _INFO_KEYS if k in im.info))
    return h.hexdigest(), params


class PillowVerifyMixin:
    def verify(self, ctx: Context, out: str) -> Tuple[Gate, Gate]:
        ctx.progress("Bütünlük: temiz dosya tam çözülüyor")
        try:
            clean_hash, clean_params = decode_digest(out)
            integrity = Gate("Bütünlük", True, ["Temiz dosyanın tüm pikselleri hatasız çözüldü (Pillow load)."])
        except Exception as e:  # noqa: BLE001 - çözücünün her hatası kapıyı kapatır
            fail = Gate("Bütünlük", False, [f"Temiz dosya çözülemedi: {e}"])
            return fail, Gate("Eşdeğerlik", False, ["Bütünlük geçilmediği için denenmedi."])
        ctx.progress("Eşdeğerlik: orijinal çözülüp karşılaştırılıyor")
        try:
            orig_hash, orig_params = decode_digest(ctx.src)
        except Exception as e:  # noqa: BLE001
            return integrity, Gate("Eşdeğerlik", False, [f"Orijinal çözülemedi, eşitlik kanıtlanamaz: {e}"])
        details = [f"Ham piksel SHA-256  orijinal: {orig_hash[:24]}…", f"Ham piksel SHA-256  temiz:    {clean_hash[:24]}…"]
        ok = orig_hash == clean_hash
        for k in orig_params.keys() | clean_params.keys():
            a, b = orig_params.get(k), clean_params.get(k)
            if a != b:
                ok = False
                details.append(f"Parametre farklı – {k}: {a!r} ≠ {b!r}")
        if ok:
            details.append("Pikseller bit düzeyinde aynı; boyut, renk modu, ICC, yön ve kare bilgileri eşleşiyor.")
        return integrity, Gate("Eşdeğerlik", ok, details)


# ---------------------------------------------------------------- JPEG

def parse_jpeg(data: bytes):
    """[(marker, payload|None)] ve EOI sonrası artık veriyi döndürür. Tarama verisi ENTROPY olarak aynen saklanır."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG SOI işareti yok")
    items: List[Tuple[int, Optional[bytes]]] = []
    pos, n = 2, len(data)
    while True:
        if pos >= n:
            raise ValueError("JPEG EOI işareti bulunamadı (dosya kesik)")
        if data[pos] != 0xFF:
            raise ValueError(f"Beklenmeyen bayt, konum {pos}")
        while pos < n and data[pos] == 0xFF:
            pos += 1
        m = data[pos]
        pos += 1
        if m == 0xD9:
            return items, data[pos:]
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            items.append((m, None))
            continue
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        if length < 2 or pos + length > n:
            raise ValueError(f"Bozuk segment uzunluğu, konum {pos}")
        items.append((m, data[pos + 2:pos + length]))
        pos += length
        if m == 0xDA:
            start = pos
            while True:
                i = data.find(b"\xff", pos)
                if i < 0 or i + 1 >= n:
                    raise ValueError("Tarama verisi bitmeden dosya bitti")
                nb = data[i + 1]
                if nb == 0x00 or 0xD0 <= nb <= 0xD7:
                    pos = i + 2
                elif nb == 0xFF:
                    pos = i + 1
                else:
                    items.append((ENTROPY, data[start:i]))
                    pos = i
                    break


def build_jpeg(items) -> bytes:
    out = bytearray(b"\xff\xd8")
    for m, p in items:
        if m == ENTROPY:
            out += p
        elif p is None:
            out += bytes((0xFF, m))
        else:
            out += bytes((0xFF, m)) + struct.pack(">H", len(p) + 2) + p
    out += b"\xff\xd9"
    return bytes(out)


def _app_name(m: int, p: bytes) -> str:
    if m == 0xE1 and p.startswith(b"Exif\x00"):
        return "EXIF (APP1) – kamera, tarih, GPS, küçük resim, üretici notları"
    if m == 0xE1 and p.startswith(b"http://ns.adobe.com/xap/1.0/"):
        return "XMP (APP1)"
    if m == 0xE1 and p.startswith(b"http://ns.adobe.com/xmp/extension/"):
        return "Genişletilmiş XMP (APP1) – derinlik haritası / ek görüntü olabilir"
    if m == 0xE2 and p.startswith(b"MPF\x00"):
        return "MPF çoklu görüntü dizini (APP2)"
    if m == 0xE2 and p.startswith(b"FPXR"):
        return "FlashPix (APP2)"
    if m == 0xED:
        return "Photoshop IRB / IPTC (APP13)"
    if m == 0xEB:
        return "JUMBF / C2PA içerik kimliği (APP11)"
    if m == 0xE0:
        return "JFXX / APP0 küçük resmi"
    tag = p[:16].split(b"\x00")[0].decode("latin-1", "replace")
    return f"APP{m - 0xE0} segmenti ({tag or 'adsız'})"


class JpegHandler(PillowVerifyMixin, Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            data = f.read()
        items, trailer = parse_jpeg(data)
        out_items = []
        info = {"orientation": None, "color_space": None}
        exif_seen = has_icc = has_adobe = jfif_kept = False
        for m, p in items:
            if m == ENTROPY or p is None or not (0xE0 <= m <= 0xEF or m == 0xFE):
                out_items.append((m, p))
            elif m == 0xE0 and p.startswith(b"JFIF\x00") and len(p) >= 12 and not jfif_kept:
                # sürüm, birim, yoğunluk kalır; gömülü küçük resim (0x0 yapılır) gider
                out_items.append((m, p[:12] + b"\x00\x00"))
                jfif_kept = True
                if len(p) > 14:
                    ctx.removed(f"JFIF küçük resmi ({len(p) - 14} bayt)")
            elif m == 0xE2 and p.startswith(b"ICC_PROFILE\x00"):
                out_items.append((m, p))
                has_icc = True
            elif m == 0xEE and p.startswith(b"Adobe"):
                out_items.append((m, p))
                has_adobe = True
            else:
                if m == 0xE1 and p.startswith(b"Exif\x00") and not exif_seen:
                    info = tiffmin.read_info(p)
                    exif_seen = True
                ctx.removed(("Yorum (COM)" if m == 0xFE else _app_name(m, p)) + f", {len(p)} bayt")
        if trailer:
            ctx.removed(f"EOI sonrası ek veri, {len(trailer)} bayt – hareketli fotoğraf videosu, "
                        "ikincil görüntü ya da üretici verisi olabilir")
        o = info["orientation"]
        if o and o != 1:
            at = 1 if out_items and out_items[0][0] == 0xE0 else 0
            out_items.insert(at, (0xE1, b"Exif\x00\x00" + tiffmin.build_orientation_tiff(o)))
            ctx.kept(f"EXIF Orientation = {o}", "Fotoğrafın doğru yönde görünmesi için (yeni, tek alanlı EXIF)")
        if has_icc:
            ctx.kept("ICC renk profili (APP2)", "Renklerin doğru görünmesi için (ör. Display P3)")
        if has_adobe:
            ctx.kept("Adobe (APP14)", "Renk dönüşümünü (YCbCr/CMYK/YCCK) belirler; silinirse renkler bozulabilir")
        if jfif_kept:
            ctx.kept("JFIF başlığı (sürüm, yoğunluk)", "Piksel en-boy oranı bilgisi; içindeki küçük resim çıkarıldı")
        if info["color_space"] == 0xFFFF and not has_icc:
            ctx.warn("EXIF ColorSpace 'Uncalibrated' ve ICC profili yok: fotoğraf Adobe RGB olabilir. "
                     "EXIF silinince bazı görüntüleyiciler renkleri sRGB varsayıp soluk gösterebilir.")
        out = ctx.out_path(".jpg")
        with open(out, "wb") as f:
            f.write(build_jpeg(out_items))
        return out

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            items, trailer = parse_jpeg(f.read())
        bad = []
        for m, p in items:
            if m == ENTROPY or p is None:
                continue
            if m == 0xE0 and p.startswith(b"JFIF\x00") and len(p) == 14:
                continue
            if m == 0xE1 and p.startswith(b"Exif\x00\x00") and tiffmin.only_orientation(p):
                continue
            if (m == 0xE2 and p.startswith(b"ICC_PROFILE\x00")) or (m == 0xEE and p.startswith(b"Adobe")):
                continue
            if 0xE0 <= m <= 0xEF or m == 0xFE:
                bad.append(f"İzin listesi dışı segment: {_app_name(m, p)}")
        if trailer:
            bad.append(f"EOI sonrası {len(trailer)} bayt veri kaldı")
        return bad


# ---------------------------------------------------------------- PNG

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT",
            b"cICP", b"mDCV", b"cLLI", b"acTL", b"fcTL", b"fdAT"}
PNG_NAMES = {b"tEXt": "Metin", b"zTXt": "Sıkıştırılmış metin", b"iTXt": "Uluslararası metin",
             b"tIME": "Son değiştirme zamanı (tIME)", b"eXIf": "EXIF (eXIf)", b"pHYs": "Fiziksel boyut / DPI (pHYs)",
             b"caBX": "C2PA içerik kimliği (caBX)", b"iDOT": "Apple iDOT", b"bKGD": "Arka plan rengi (bKGD)",
             b"hIST": "Histogram (hIST)", b"sPLT": "Önerilen palet (sPLT)", b"vpAg": "vpAg", b"orNT": "orNT"}


def parse_png(data: bytes):
    if not data.startswith(PNG_SIG):
        raise ValueError("PNG imzası yok")
    pos, chunks = 8, []
    while True:
        if pos + 12 > len(data):
            raise ValueError("PNG IEND bulunamadı (dosya kesik)")
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise ValueError("PNG chunk'ı dosya sonunu aşıyor")
        chunks.append((ctype, data[pos + 8:pos + 8 + length], data[pos:end]))
        pos = end
        if ctype == b"IEND":
            return chunks, data[pos:]


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))


class PngHandler(PillowVerifyMixin, Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            chunks, trailer = parse_png(f.read())
        orientation = next((tiffmin.read_info(p)["orientation"] for t, p, _ in chunks if t == b"eXIf"), None)
        out = [PNG_SIG]
        kept_types = set()
        for ctype, payload, raw in chunks:
            if ctype in PNG_KEEP:
                if ctype == b"IDAT" and orientation and orientation != 1 and b"eXIf" not in kept_types:
                    out.append(_png_chunk(b"eXIf", tiffmin.build_orientation_tiff(orientation)))
                    kept_types.add(b"eXIf")
                out.append(raw)
                kept_types.add(ctype)
                continue
            if 65 <= ctype[0] <= 90:  # büyük harf = kritik chunk; bilinmeyeni atamayız
                raise ValueError(f"Desteklenmeyen kritik PNG chunk'ı: {ctype.decode('latin-1')}")
            name = PNG_NAMES.get(ctype, ctype.decode("latin-1", "replace"))
            if ctype in (b"tEXt", b"zTXt", b"iTXt"):
                keyword = payload.split(b"\x00")[0].decode("latin-1", "replace")
                name += f" ({keyword})"
            ctx.removed(f"{name}, {len(payload)} bayt")
        if trailer:
            ctx.removed(f"IEND sonrası ek veri, {len(trailer)} bayt")
        if b"eXIf" in kept_types:
            ctx.kept(f"EXIF Orientation = {orientation}", "Doğru yönde görünmesi için (yeni, tek alanlı eXIf)")
        if b"iCCP" in kept_types:
            ctx.kept("ICC renk profili (iCCP)", "Renklerin doğru görünmesi için")
        for t, what in ((b"gAMA", "Gama (gAMA)"), (b"cHRM", "Renk koordinatları (cHRM)"), (b"sRGB", "sRGB amacı"),
                        (b"cICP", "Renk/HDR kodlama (cICP)"), (b"mDCV", "HDR ekran bilgisi (mDCV)"),
                        (b"cLLI", "HDR parlaklık (cLLI)")):
            if t in kept_types:
                ctx.kept(what, "Renk/parlaklık yorumunu belirler")
        if b"acTL" in kept_types:
            ctx.kept("APNG animasyon chunk'ları", "Animasyonun kendisi")
        path = ctx.out_path(".png")
        with open(path, "wb") as f:
            f.write(b"".join(out))
        return path

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            chunks, trailer = parse_png(f.read())
        bad = [f"İzin listesi dışı chunk: {t.decode('latin-1')}" for t, p, _ in chunks
               if t not in PNG_KEEP and not (t == b"eXIf" and tiffmin.only_orientation(p))]
        if trailer:
            bad.append(f"IEND sonrası {len(trailer)} bayt veri kaldı")
        return bad


# ---------------------------------------------------------------- WebP

WEBP_KEEP = {b"VP8X", b"ICCP", b"ANIM", b"ANMF", b"ALPH", b"VP8 ", b"VP8L"}


def parse_riff(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("WebP RIFF başlığı yok")
    end = 8 + struct.unpack("<I", data[4:8])[0]
    if end > len(data):
        raise ValueError("WebP dosyası kesik")
    pos, chunks = 12, []
    while pos + 8 <= end:
        fourcc, length = struct.unpack("<4sI", data[pos:pos + 8])
        if pos + 8 + length > end:
            raise ValueError("WebP chunk'ı dosya sonunu aşıyor")
        chunks.append((fourcc, data[pos + 8:pos + 8 + length]))
        pos += 8 + length + (length & 1)
    return chunks, data[end:]


def build_riff(chunks) -> bytes:
    body = b"".join(fc + struct.pack("<I", len(p)) + p + (b"\x00" if len(p) & 1 else b"") for fc, p in chunks)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


class WebpHandler(PillowVerifyMixin, Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            chunks, trailer = parse_riff(f.read())
        extended = chunks and chunks[0][0] == b"VP8X"
        orientation = None
        out = []
        for fc, p in chunks:
            if fc in WEBP_KEEP and (extended or (fc in (b"VP8 ", b"VP8L") and not out)):
                out.append((fc, p))
                continue
            if fc == b"EXIF":
                orientation = tiffmin.read_info(p)["orientation"]
            name = {b"EXIF": "EXIF", b"XMP ": "XMP"}.get(fc, fc.decode("latin-1", "replace").strip())
            ctx.removed(f"{name} chunk'ı, {len(p)} bayt")
        if trailer:
            ctx.removed(f"RIFF sonrası ek veri, {len(trailer)} bayt")
        if extended:
            flags = out[0][1][0] & ~0x0C  # EXIF (0x08) ve XMP (0x04) bayrakları
            if orientation and orientation != 1:
                out.append((b"EXIF", tiffmin.build_orientation_tiff(orientation)))
                flags |= 0x08
                ctx.kept(f"EXIF Orientation = {orientation}", "Doğru yönde görünmesi için (yeni, tek alanlı EXIF)")
            out[0] = (b"VP8X", bytes([flags]) + out[0][1][1:])
            if any(fc == b"ICCP" for fc, _ in out):
                ctx.kept("ICC renk profili (ICCP)", "Renklerin doğru görünmesi için")
        path = ctx.out_path(".webp")
        with open(path, "wb") as f:
            f.write(build_riff(out))
        return path

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            chunks, trailer = parse_riff(f.read())
        bad = [f"İzin listesi dışı chunk: {fc.decode('latin-1')}" for fc, p in chunks
               if fc not in WEBP_KEEP and not (fc == b"EXIF" and tiffmin.only_orientation(p))]
        if trailer:
            bad.append(f"RIFF sonrası {len(trailer)} bayt veri kaldı")
        return bad


# ---------------------------------------------------------------- HEIC / AVIF

class HeifHandler(PillowVerifyMixin, Handler):
    """ISO-BMFF tabanlı görseller. Blokları ExifTool siler; ICC ve Orientation korunur."""

    def __init__(self, ext: str):
        self.ext = ext

    def clean(self, ctx: Context) -> str:
        out = ctx.out_path(self.ext)
        tools.exiftool_write(["-all=", "--icc_profile:all", "-tagsfromfile", "@", "-exif:orientation",
                              "-o", out, ctx.src], log=ctx.log, cancel=ctx.cancel)
        ctx.removed("EXIF, XMP, IPTC ve üretici notları (ExifTool -all=)")
        ctx.kept("ICC renk profili / colr", "Renklerin doğru görünmesi için")
        ctx.kept("EXIF Orientation ile irot/imir özellikleri", "Doğru yönde görünmesi için")
        try:
            import pillow_heif
            heif = pillow_heif.open_heif(ctx.src)
            thumbs = sum(len(img.info.get("thumbnails", [])) for img in heif)
            depth = sum(len(img.info.get("depth_images", [])) for img in heif)
            if thumbs:
                ctx.warn(f"Dosyada {thumbs} küçük resim öğesi var. Bunlar meta veri değil görüntü öğesidir; "
                         "ExifTool silemez. Kırpılmış bir fotoğrafsa kırpılmamış hâli gösterebilir.")
            if depth:
                ctx.warn(f"Dosyada {depth} derinlik haritası var; ExifTool bunları silemez.")
            if len(heif) > 1:
                ctx.warn(f"Dosyada {len(heif)} ana görüntü var; hepsi korunur.")
        except Exception:  # noqa: BLE001 - yalnızca uyarı amaçlı
            pass
        return out
