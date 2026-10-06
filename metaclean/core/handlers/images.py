"""Görseller: JPEG, PNG, WebP blok düzeyinde (yeniden sıkıştırma yok); HEIC/AVIF ExifTool ile."""
from __future__ import annotations

import hashlib
import io
import struct
import zlib
from typing import Dict, List, Optional, Tuple

from ...i18n import tr
from .. import tiffmin, tools
from .. import heifcheck
from . import gainmap
from ..report import Gate
from .base import Context, Handler

ENTROPY = -1

# ---------------------------------------------------------------- Pillow ile çözme

_INFO_KEYS = ("gamma", "chromaticity", "srgb", "transparency", "adobe", "adobe_transform",
              "jfif_unit", "jfif_density", "progressive", "loop", "duration", "disposal", "blend",
              "background")


def _pil():
    from PIL import Image
    # Sıkıştırma bombası koruması: dosyalar internetten gelebilir. Birkaç KB'lık ama on binlerce piksel
    # genişliğinde olduğunu söyleyen bir dosya gigabaytlarca bellek isteyip uygulamayı çökertebilir.
    # 200 MP en yüksek çözünürlüklü telefonları kapsar; Pillow bunun iki katını aşanı reddeder.
    Image.MAX_IMAGE_PIXELS = 200_000_000
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image


def _param_label(key: str) -> str:
    """Parametre adının görünen hâli; "kare 3" gibi kare anahtarları da çevrilir."""
    if key.startswith("kare "):
        return tr("kare {i}", i=key[5:])
    return tr(key)


def decode_digest(path) -> Tuple[str, Dict[str, object]]:
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
        ctx.progress(tr("Bütünlük: temiz dosya tam çözülüyor"))
        try:
            clean_hash, clean_params = decode_digest(out)
            integrity = Gate(tr("Bütünlük"), True, [tr("Temiz dosyanın tüm pikselleri hatasız çözüldü (Pillow load).")])
        except Exception as e:  # noqa: BLE001 - çözücünün her hatası kapıyı kapatır
            fail = Gate(tr("Bütünlük"), False, [tr("Temiz dosya çözülemedi: {e}", e=e)])
            return fail, Gate(tr("Eşdeğerlik"), False, [tr("Bütünlük geçilmediği için denenmedi.")])
        ctx.progress(tr("Eşdeğerlik: orijinal çözülüp karşılaştırılıyor"))
        try:
            orig_hash, orig_params = decode_digest(ctx.src)
        except Exception as e:  # noqa: BLE001
            return integrity, Gate(tr("Eşdeğerlik"), False, [tr("Orijinal çözülemedi, eşitlik kanıtlanamaz: {e}", e=e)])
        details = [f"Ham piksel SHA-256  orijinal: {orig_hash[:24]}…", f"Ham piksel SHA-256  temiz:    {clean_hash[:24]}…"]
        ok = orig_hash == clean_hash
        for k in orig_params.keys() | clean_params.keys():
            a, b = orig_params.get(k), clean_params.get(k)
            if a != b:
                ok = False
                details.append(tr("Parametre farklı – {k}: {a} ≠ {b}", k=_param_label(k), a=repr(a), b=repr(b)))
        if ok:
            details.append(tr("Pikseller bit düzeyinde aynı; boyut, renk modu, ICC, yön ve kare bilgileri eşleşiyor."))
        return integrity, Gate(tr("Eşdeğerlik"), ok, details)


# ---------------------------------------------------------------- JPEG

def parse_jpeg(data: bytes):
    """[(marker, payload|None)] ve EOI sonrası artık veriyi döndürür. Tarama verisi ENTROPY olarak aynen saklanır."""
    if data[:2] != b"\xff\xd8":
        raise ValueError(tr("JPEG SOI işareti yok"))
    items: List[Tuple[int, Optional[bytes]]] = []
    pos, n = 2, len(data)
    while True:
        if pos >= n:
            raise ValueError(tr("JPEG EOI işareti bulunamadı (dosya kesik)"))
        if data[pos] != 0xFF:
            raise ValueError(tr("Beklenmeyen bayt, konum {pos}", pos=pos))
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
            raise ValueError(tr("Bozuk segment uzunluğu, konum {pos}", pos=pos))
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
        return tr("EXIF (APP1) – kamera, tarih, GPS, küçük resim, üretici notları")
    if m == 0xE1 and p.startswith(b"http://ns.adobe.com/xap/1.0/"):
        return "XMP (APP1)"
    if m == 0xE1 and p.startswith(b"http://ns.adobe.com/xmp/extension/"):
        return tr("Genişletilmiş XMP (APP1) – derinlik haritası / ek görüntü olabilir")
    if m == 0xE2 and p.startswith(b"MPF\x00"):
        return tr("MPF çoklu görüntü dizini (APP2)")
    if m == 0xE2 and p.startswith(b"FPXR"):
        return "FlashPix (APP2)"
    if m == 0xED:
        return "Photoshop IRB / IPTC (APP13)"
    if m == 0xEB:
        return tr("JUMBF / C2PA içerik kimliği (APP11)")
    if m == 0xE0:
        return tr("JFXX / APP0 küçük resmi")
    tag = p[:16].split(b"\x00")[0].decode("latin-1", "replace")
    return tr("APP{n} segmenti ({tag})", n=m - 0xE0, tag=tag or tr("adsız"))


ICC_SIG = b"ICC_PROFILE\x00"


def _is_mpf(m: int, p: Optional[bytes]) -> bool:
    return m == 0xE2 and p is not None and p.startswith(gainmap.MPF_SIG)


def _secondaries(data: bytes, items, trailer: bytes) -> List[Tuple[int, bytes]]:
    """EOI sonrasındaki ikincil JPEG'ler: [(MPF özniteliği, bayt)]. Önce MPF dizinine, yoksa
    art arda gelen JPEG'lere bakılır."""
    out: List[Tuple[int, bytes]] = []
    mpf = next((p for m, p in items if _is_mpf(m, p)), None)
    base = gainmap.mpf_tiff_offset(data) if mpf else None
    if mpf and base:
        try:
            entries = gainmap.parse_mpf(mpf)
        except (struct.error, IndexError):
            entries = []
        for attr, size, offset in entries[1:]:
            chunk = data[base + offset:base + offset + size]
            if len(chunk) == size and chunk[:2] == b"\xff\xd8":
                out.append((attr, chunk))
    if not out:
        rest = trailer
        while rest[:2] == b"\xff\xd8":
            try:
                _, tail = parse_jpeg(rest)
            except ValueError:
                break
            out.append((0, rest[:len(rest) - len(tail)]))
            rest = tail
    return out


def _clean_gain_map(jpeg: bytes) -> bytes:
    """Kazanç haritasının kendi meta verisini temizler; yalnızca HDR parametreleri kalır."""
    items, _ = parse_jpeg(jpeg)
    keep = []
    for m, p in items:
        if m == ENTROPY or p is None or not (0xE0 <= m <= 0xEF or m == 0xFE):
            keep.append((m, p))
        elif m == 0xE0 and p.startswith(b"JFIF\x00") and len(p) >= 12:
            keep.append((m, p[:12] + b"\x00\x00"))
        elif (m == 0xE2 and (p.startswith(ICC_SIG) or p.startswith(gainmap.ISO_SIG))) or \
                (m == 0xEE and p.startswith(b"Adobe")):
            keep.append((m, p))
        elif m == 0xE1 and p.startswith(gainmap.XMP_SIG):
            x = gainmap.sanitize_xmp(p, gainmap.GAIN_MAP_NAMESPACES)
            if x:
                keep.append((0xE1, x))
    return build_jpeg(keep)


def canonical_parts(data: bytes) -> Tuple[bytes, List[bytes]]:
    """Karşılaştırma için: MPF'siz ana görüntü ve kazanç haritaları. Pikseller bunlardan çözülür;
    böylece silinen derinlik haritası gibi ikincil görüntüler eşdeğerliği bozmaz."""
    items, trailer = parse_jpeg(data)
    gains = [j for _, j in _secondaries(data, items, trailer) if gainmap.is_gain_map(parse_jpeg(j)[0])]
    return build_jpeg([(m, p) for m, p in items if not _is_mpf(m, p)]), gains


class JpegHandler(Handler):
    def clean(self, ctx: Context) -> str:
        with open(ctx.src, "rb") as f:
            data = f.read()
        items, trailer = parse_jpeg(data)
        out_items = []
        info = {"orientation": None, "color_space": None}
        exif_seen = has_icc = has_adobe = jfif_kept = ultra_hdr = has_iso = False
        for m, p in items:
            if m == ENTROPY or p is None or not (0xE0 <= m <= 0xEF or m == 0xFE):
                out_items.append((m, p))
            elif m == 0xE0 and p.startswith(b"JFIF\x00") and len(p) >= 12 and not jfif_kept:
                # sürüm, birim, yoğunluk kalır; gömülü küçük resim (0x0 yapılır) gider
                out_items.append((m, p[:12] + b"\x00\x00"))
                jfif_kept = True
                if len(p) > 14:
                    ctx.removed(tr("JFIF küçük resmi ({n} bayt)", n=len(p) - 14))
            elif m == 0xE2 and p.startswith(ICC_SIG):
                out_items.append((m, p))
                has_icc = True
            elif m == 0xE2 and p.startswith(gainmap.ISO_SIG):
                out_items.append((m, p))  # ISO 21496-1 kazanç haritası sürüm bilgisi; kişisel veri yok
                has_iso = True
            elif m == 0xEE and p.startswith(b"Adobe"):
                out_items.append((m, p))
                has_adobe = True
            elif _is_mpf(m, p):
                pass  # dizin, korunan görüntülere göre aşağıda yeniden kurulur
            else:
                if m == 0xE1 and p.startswith(b"Exif\x00") and not exif_seen:
                    info = tiffmin.read_info(p)
                    exif_seen = True
                ultra_hdr |= gainmap.has_hdrgm(p) if m == 0xE1 else False
                ctx.removed(tr("{name}, {n} bayt", name=tr("Yorum (COM)") if m == 0xFE else _app_name(m, p), n=len(p)))

        secondaries = _secondaries(data, items, trailer)
        gains: List[Tuple[int, bytes]] = []
        apple_gain = False
        for attr, jpeg in secondaries:
            seg = parse_jpeg(jpeg)[0]
            if gainmap.is_gain_map(seg):
                gains.append((attr, _clean_gain_map(jpeg)))
                apple_gain |= any(p is not None and gainmap.APPLE_GAIN_TYPE.encode() in p for _, p in seg)
            else:
                ctx.removed(tr("İkincil görüntü (derinlik haritası, önizleme vb.), {n} bayt", n=len(jpeg)))
        leftover = len(trailer) - sum(len(j) for _, j in secondaries)
        if leftover > 0:
            ctx.removed(tr("EOI sonrası ek veri, {n} bayt – hareketli fotoğraf videosu ya da üretici verisi olabilir", n=leftover))

        o = info["orientation"]
        head = 1 if out_items and out_items[0][0] == 0xE0 else 0
        if o and o != 1:
            out_items.insert(head, (0xE1, b"Exif\x00\x00" + tiffmin.build_orientation_tiff(o)))
            head += 1
            ctx.kept(f"EXIF Orientation = {o}", tr("Fotoğrafın doğru yönde görünmesi için (yeni, tek alanlı EXIF)"))
        if gains and ultra_hdr:
            out_items.insert(head, (0xE1, gainmap.primary_xmp([len(j) for _, j in gains])))
        if has_icc:
            ctx.kept(tr("ICC renk profili (APP2)"), tr("Renklerin doğru görünmesi için (ör. Display P3)"))
        if has_adobe:
            ctx.kept("Adobe (APP14)", tr("Renk dönüşümünü (YCbCr/CMYK/YCCK) belirler; silinirse renkler bozulabilir"))
        if jfif_kept:
            ctx.kept(tr("JFIF başlığı (sürüm, yoğunluk)"), tr("Piksel en-boy oranı bilgisi; içindeki küçük resim çıkarıldı"))
        if info["color_space"] == 0xFFFF and not has_icc:
            ctx.warn(tr("EXIF ColorSpace 'Uncalibrated' ve ICC profili yok: fotoğraf Adobe RGB olabilir. "
                        "EXIF silinince bazı görüntüleyiciler renkleri sRGB varsayıp soluk gösterebilir."))

        if gains:
            ctx.kept(tr("HDR kazanç haritası (ikincil görüntü)"),
                     tr("HDR ekranlarda parlaklığı geri kurar; kendi EXIF/XMP'si temizlendi, yalnızca HDR parametreleri kaldı"))
            ctx.kept(tr("MPF görüntü dizini"), tr("Kazanç haritasının dosyadaki yerini gösterir (yeniden hesaplandı)"))
            if ultra_hdr:
                ctx.kept(tr("Ultra HDR XMP (hdrgm sürümü, GContainer dizini)"), tr("Android ve tarayıcılar kazanç haritasını bununla bulur"))
            if has_iso:
                ctx.kept(tr("ISO 21496-1 kazanç haritası bilgisi (APP2)"), tr("Standart HDR görüntüleme parametreleri"))
            if apple_gain:
                ctx.warn(tr("Apple HDR fotoğrafı: parlaklık payı (headroom) üretici notlarında saklanıyor ve kişisel "
                            "verilerle birlikte silindi. HDR ekranda parlaklık varsayılan değerle gösterilebilir."))
            out = self._with_gain_maps(out_items, gains)
        else:
            out = build_jpeg(out_items)
        path = ctx.out_path(".jpg")
        with open(path, "wb") as f:
            f.write(out)
        return path

    @staticmethod
    def _with_gain_maps(items, gains: List[Tuple[int, bytes]]) -> bytes:
        # MPF'yi ilk APP olmayan segmentten (DQT/SOF) hemen önceye koy; boyutu değerlerden bağımsız
        at = next(i for i, (m, _) in enumerate(items) if not (0xE0 <= m <= 0xEF))
        placeholder = gainmap.build_mpf([(0, 0, 0)] * (1 + len(gains)))
        draft = build_jpeg(items[:at] + [(0xE2, placeholder)] + items[at:])
        base = gainmap.mpf_tiff_offset(draft)
        entries = [(gainmap.PRIMARY_ATTR, len(draft), 0)]
        pos = len(draft)
        for attr, jpeg in gains:
            entries.append((attr, len(jpeg), pos - base))
            pos += len(jpeg)
        primary = build_jpeg(items[:at] + [(0xE2, gainmap.build_mpf(entries))] + items[at:])
        return primary + b"".join(j for _, j in gains)

    def verify(self, ctx: Context, out: str) -> Tuple[Gate, Gate]:
        ctx.progress(tr("Bütünlük: temiz dosya tam çözülüyor"))
        try:
            decode_digest(out)  # MPF varsa Pillow tüm görüntüleri (MPO kareleri) çözer
            with open(out, "rb") as f:
                clean_primary, clean_gains = canonical_parts(f.read())
            clean_hash, clean_params = decode_digest(io.BytesIO(clean_primary))
            clean_gain_hashes = [decode_digest(io.BytesIO(g))[0] for g in clean_gains]
        except Exception as e:  # noqa: BLE001 - çözücünün her hatası kapıyı kapatır
            return (Gate(tr("Bütünlük"), False, [tr("Temiz dosya çözülemedi: {e}", e=e)]),
                    Gate(tr("Eşdeğerlik"), False, [tr("Bütünlük geçilmediği için denenmedi.")]))
        integrity = Gate(tr("Bütünlük"), True, [tr("Temiz dosyanın tüm görüntüleri hatasız çözüldü (Pillow load).")])
        ctx.progress(tr("Eşdeğerlik: orijinal çözülüp karşılaştırılıyor"))
        try:
            with open(ctx.src, "rb") as f:
                orig_primary, orig_gains = canonical_parts(f.read())
            orig_hash, orig_params = decode_digest(io.BytesIO(orig_primary))
            orig_gain_hashes = [decode_digest(io.BytesIO(g))[0] for g in orig_gains]
        except Exception as e:  # noqa: BLE001
            return integrity, Gate(tr("Eşdeğerlik"), False, [tr("Orijinal çözülemedi, eşitlik kanıtlanamaz: {e}", e=e)])
        details = [tr("Ana görüntü piksel SHA-256  orijinal: {h}…", h=orig_hash[:24]),
                   tr("Ana görüntü piksel SHA-256  temiz:    {h}…", h=clean_hash[:24])]
        ok = orig_hash == clean_hash
        for k in orig_params.keys() | clean_params.keys():
            if orig_params.get(k) != clean_params.get(k):
                ok = False
                details.append(tr("Parametre farklı – {k}: {a} ≠ {b}", k=_param_label(k), a=repr(orig_params.get(k)),
                                  b=repr(clean_params.get(k))))
        if orig_gain_hashes or clean_gain_hashes:
            same = orig_gain_hashes == clean_gain_hashes
            ok &= same
            details.append(tr("HDR kazanç haritası: {a} → {b}, {result}", a=len(orig_gain_hashes), b=len(clean_gain_hashes),
                              result=tr("pikseller bit düzeyinde aynı.") if same else tr("FARKLI ya da eksik.")))
        if ok:
            details.append(tr("Pikseller bit düzeyinde aynı; boyut, renk modu, ICC, yön bilgileri eşleşiyor."))
        return integrity, Gate(tr("Eşdeğerlik"), ok, details)

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            data = f.read()
        items, trailer = parse_jpeg(data)
        bad = []
        for m, p in items:
            if m == ENTROPY or p is None:
                continue
            if m == 0xE0 and p.startswith(b"JFIF\x00") and len(p) == 14:
                continue
            if m == 0xE1 and p.startswith(b"Exif\x00\x00") and tiffmin.only_orientation(p):
                continue
            if m == 0xE1 and p.startswith(gainmap.XMP_SIG) and gainmap.xmp_namespaces(p) <= gainmap.PRIMARY_NAMESPACES:
                continue
            if m == 0xE2 and (p.startswith(ICC_SIG) or p.startswith(gainmap.ISO_SIG) or p.startswith(gainmap.MPF_SIG)):
                continue
            if m == 0xEE and p.startswith(b"Adobe"):
                continue
            if 0xE0 <= m <= 0xEF or m == 0xFE:
                bad.append(tr("İzin listesi dışı segment: {name}", name=_app_name(m, p)))
        secondaries = _secondaries(data, items, trailer)
        for _, jpeg in secondaries:
            seg = parse_jpeg(jpeg)[0]
            if not gainmap.is_gain_map(seg):
                bad.append(tr("Kazanç haritası olmayan ikincil görüntü kaldı ({n} bayt)", n=len(jpeg)))
            for m, p in seg:
                if p is None or m == ENTROPY or not (0xE0 <= m <= 0xEF or m == 0xFE):
                    continue
                if m == 0xE1 and p.startswith(gainmap.XMP_SIG) and gainmap.xmp_namespaces(p) <= gainmap.GAIN_MAP_NAMESPACES:
                    continue
                if (m == 0xE0 and len(p) == 14) or (m == 0xE2 and (p.startswith(ICC_SIG) or p.startswith(gainmap.ISO_SIG))) \
                        or (m == 0xEE and p.startswith(b"Adobe")):
                    continue
                bad.append(tr("Kazanç haritasında izin listesi dışı segment: {name}", name=_app_name(m, p)))
        leftover = len(trailer) - sum(len(j) for _, j in secondaries)
        if leftover:
            bad.append(tr("EOI sonrası {n} bayt tanımsız veri kaldı", n=leftover))
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
        raise ValueError(tr("PNG imzası yok"))
    pos, chunks = 8, []
    while True:
        if pos + 12 > len(data):
            raise ValueError(tr("PNG IEND bulunamadı (dosya kesik)"))
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if end > len(data):
            raise ValueError(tr("PNG chunk'ı dosya sonunu aşıyor"))
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
                raise ValueError(tr("Desteklenmeyen kritik PNG chunk'ı: {c}", c=ctype.decode("latin-1")))
            name = tr(PNG_NAMES[ctype]) if ctype in PNG_NAMES else ctype.decode("latin-1", "replace")
            if ctype in (b"tEXt", b"zTXt", b"iTXt"):
                keyword = payload.split(b"\x00")[0].decode("latin-1", "replace")
                name += f" ({keyword})"
            ctx.removed(tr("{name}, {n} bayt", name=name, n=len(payload)))
        if trailer:
            ctx.removed(tr("IEND sonrası ek veri, {n} bayt", n=len(trailer)))
        if b"eXIf" in kept_types:
            ctx.kept(f"EXIF Orientation = {orientation}", tr("Doğru yönde görünmesi için (yeni, tek alanlı eXIf)"))
        if b"iCCP" in kept_types:
            ctx.kept(tr("ICC renk profili (iCCP)"), tr("Renklerin doğru görünmesi için"))
        for t, what in ((b"gAMA", "Gama (gAMA)"), (b"cHRM", "Renk koordinatları (cHRM)"), (b"sRGB", "sRGB amacı"),
                        (b"cICP", "Renk/HDR kodlama (cICP)"), (b"mDCV", "HDR ekran bilgisi (mDCV)"),
                        (b"cLLI", "HDR parlaklık (cLLI)")):
            if t in kept_types:
                ctx.kept(tr(what), tr("Renk/parlaklık yorumunu belirler"))
        if b"acTL" in kept_types:
            ctx.kept(tr("APNG animasyon chunk'ları"), tr("Animasyonun kendisi"))
        path = ctx.out_path(".png")
        with open(path, "wb") as f:
            f.write(b"".join(out))
        return path

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            chunks, trailer = parse_png(f.read())
        bad = [tr("İzin listesi dışı chunk: {c}", c=t.decode("latin-1")) for t, p, _ in chunks
               if t not in PNG_KEEP and not (t == b"eXIf" and tiffmin.only_orientation(p))]
        if trailer:
            bad.append(tr("IEND sonrası {n} bayt veri kaldı", n=len(trailer)))
        return bad


# ---------------------------------------------------------------- WebP

WEBP_KEEP = {b"VP8X", b"ICCP", b"ANIM", b"ANMF", b"ALPH", b"VP8 ", b"VP8L"}


def parse_riff(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError(tr("WebP RIFF başlığı yok"))
    end = 8 + struct.unpack("<I", data[4:8])[0]
    if end > len(data):
        raise ValueError(tr("WebP dosyası kesik"))
    pos, chunks = 12, []
    while pos + 8 <= end:
        fourcc, length = struct.unpack("<4sI", data[pos:pos + 8])
        if pos + 8 + length > end:
            raise ValueError(tr("WebP chunk'ı dosya sonunu aşıyor"))
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
            ctx.removed(tr("{name} chunk'ı, {n} bayt", name=name, n=len(p)))
        if trailer:
            ctx.removed(tr("RIFF sonrası ek veri, {n} bayt", n=len(trailer)))
        if extended:
            flags = out[0][1][0] & ~0x0C  # EXIF (0x08) ve XMP (0x04) bayrakları
            if orientation and orientation != 1:
                out.append((b"EXIF", tiffmin.build_orientation_tiff(orientation)))
                flags |= 0x08
                ctx.kept(f"EXIF Orientation = {orientation}", tr("Doğru yönde görünmesi için (yeni, tek alanlı EXIF)"))
            out[0] = (b"VP8X", bytes([flags]) + out[0][1][1:])
            if any(fc == b"ICCP" for fc, _ in out):
                ctx.kept(tr("ICC renk profili (ICCP)"), tr("Renklerin doğru görünmesi için"))
        path = ctx.out_path(".webp")
        with open(path, "wb") as f:
            f.write(build_riff(out))
        return path

    def structural(self, ctx: Context, out: str) -> List[str]:
        with open(out, "rb") as f:
            chunks, trailer = parse_riff(f.read())
        bad = [tr("İzin listesi dışı chunk: {c}", c=fc.decode("latin-1")) for fc, p in chunks
               if fc not in WEBP_KEEP and not (fc == b"EXIF" and tiffmin.only_orientation(p))]
        if trailer:
            bad.append(tr("RIFF sonrası {n} bayt veri kaldı", n=len(trailer)))
        return bad


# ---------------------------------------------------------------- HEIC / AVIF

class HeifHandler(PillowVerifyMixin, Handler):
    """ISO-BMFF tabanlı görseller. Blokları ExifTool siler; ICC ve Orientation korunur."""

    def __init__(self, ext: str):
        self.ext = ext

    def structural(self, ctx: Context, out: str) -> Optional[List[str]]:
        # ExifTool tanımadığı öğeyi silmez ve yeniden taramada göstermez; yapı ayrıca denetlenir
        return heifcheck.problems(out)

    def clean(self, ctx: Context) -> str:
        out = ctx.out_path(self.ext)
        tools.exiftool_write(["-all=", "--icc_profile:all", "-tagsfromfile", "@", "-exif:orientation",
                              "-o", out, ctx.src], log=ctx.log, cancel=ctx.cancel)
        ctx.removed(tr("EXIF, XMP, IPTC ve üretici notları (ExifTool -all=)"))
        ctx.kept(tr("ICC renk profili / colr"), tr("Renklerin doğru görünmesi için"))
        ctx.kept(tr("EXIF Orientation ile irot/imir özellikleri"), tr("Doğru yönde görünmesi için"))
        try:
            import pillow_heif
            heif = pillow_heif.open_heif(ctx.src)
            thumbs = sum(len(img.info.get("thumbnails", [])) for img in heif)
            depth = sum(len(img.info.get("depth_images", [])) for img in heif)
            if thumbs:
                ctx.warn(tr("Dosyada {n} küçük resim öğesi var. Bunlar meta veri değil görüntü öğesidir; "
                            "ExifTool silemez. Kırpılmış bir fotoğrafsa kırpılmamış hâli gösterebilir.", n=thumbs))
            if depth:
                ctx.warn(tr("Dosyada {n} derinlik haritası var; ExifTool bunları silemez.", n=depth))
            if len(heif) > 1:
                ctx.warn(tr("Dosyada {n} ana görüntü var; hepsi korunur.", n=len(heif)))
        except Exception:  # noqa: BLE001 - yalnızca uyarı amaçlı
            pass
        return out
