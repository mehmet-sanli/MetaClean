"""HEIC/AVIF (ISO-BMFF) çıktısının yapısal denetimi: yalnızca izin listesindeki kutular, öğeler ve özellikler.

Temizliği ExifTool yapar; ama ExifTool tanımadığı bir öğeyi (ör. türü bozulmuş bir EXIF öğesi) ne siler ne de
yeniden taramada gösterir. mdat'ta hiçbir öğeye ait olmayan (bağlantısı koparılmış) baytları da aynen taşır.
Bu denetim JPEG/PNG/WebP'deki gibi kapalı varsayılanla çalışır: bilinmeyen her şey temizliği durdurur.
"""
from __future__ import annotations

import struct
from typing import Dict, Iterator, List, Tuple

from ..i18n import tr

TOP_LEVEL = {b"ftyp", b"meta", b"mdat"}
META_CHILDREN = {b"hdlr", b"pitm", b"iloc", b"iinf", b"iprp", b"iref", b"idat", b"dinf", b"grpl"}
# Görüntü öğeleri ve yalnızca yön taşıyan Exif (içeriğini ExifTool yeniden taraması denetler)
ITEM_TYPES = {"hvc1", "av01", "grid", "iden", "iovl", "tmap", "jpeg", "Exif"}
# Görüntülemeyi belirleyen teknik özellikler; metin taşıyabilenler (ör. udes açıklaması) listede yok
PROPERTIES = {b"hvcC", b"av1C", b"jpgC", b"ispe", b"pixi", b"colr", b"irot", b"imir", b"clap", b"pasp", b"auxC",
              b"clli", b"mdcv", b"cclv", b"amve", b"rloc", b"lsel", b"a1op", b"a1lx", b"ccst"}


def _boxes(buf: bytes, start: int, end: int) -> Iterator[Tuple[bytes, int, int]]:
    """(tür, içerik başı, kutu sonu)"""
    i = start
    while i < end:
        if i + 8 > end:
            raise ValueError(tr("HEIF kutusu kesik"))
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", buf[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            raise ValueError(tr("HEIF kutusu dosya sonunu aşıyor"))
        yield typ, i + head, i + size
        i += size


def _uint(buf: bytes, pos: int, n: int) -> Tuple[int, int]:
    return (int.from_bytes(buf[pos:pos + n], "big") if n else 0), pos + n


def _items(buf: bytes, start: int, end: int) -> Dict[int, str]:
    """iinf: öğe kimliği -> öğe türü"""
    version = buf[start]
    pos = start + 4
    _, pos = _uint(buf, pos, 2 if version == 0 else 4)
    items = {}
    for typ, s, _e in _boxes(buf, pos, end):
        if typ != b"infe" or buf[s] < 2:
            raise ValueError(tr("Desteklenmeyen HEIF öğe kaydı"))
        item_id, p = _uint(buf, s + 4, 2 if buf[s] == 2 else 4)
        items[item_id] = buf[p + 2:p + 6].decode("latin-1")
    return items


def _locations(buf: bytes, start: int) -> List[Tuple[int, int, int, int]]:
    """iloc: [(öğe, yapım yöntemi, konum, uzunluk)]"""
    version = buf[start]
    pos = start + 4
    sizes = buf[pos]
    offset_size, length_size = sizes >> 4, sizes & 15
    sizes = buf[pos + 1]
    base_size, index_size = sizes >> 4, (sizes & 15) if version in (1, 2) else 0
    pos += 2
    count, pos = _uint(buf, pos, 2 if version < 2 else 4)
    out = []
    for _ in range(count):
        item_id, pos = _uint(buf, pos, 2 if version < 2 else 4)
        method = 0
        if version in (1, 2):
            method, pos = _uint(buf, pos, 2)
            method &= 15
        pos += 2  # data_reference_index
        base, pos = _uint(buf, pos, base_size)
        extents, pos = _uint(buf, pos, 2)
        for _ in range(extents):
            pos += index_size
            offset, pos = _uint(buf, pos, offset_size)
            length, pos = _uint(buf, pos, length_size)
            out.append((item_id, method, base + offset, length))
    return out


def _orphans(buf: bytes, start: int, end: int, extents: List[Tuple[int, int]]) -> int:
    """[start, end) içinde hiçbir öğenin kapsamadığı, sıfır olmayan bayt sayısı (sıfırlar hizalama dolgusu)."""
    # Uzunluk 0 "boş" sayılır: sahte bir uzunluk-0 kaydı gizli baytları "kapsıyor" gibi gösteremesin
    spans = sorted((max(off, start), min(off + length, end)) for off, length in extents)
    hidden, pos = 0, start
    for lo, hi in spans:
        if hi <= lo:
            continue
        if lo > pos:
            hidden += (lo - pos) - buf[pos:lo].count(0)
        pos = max(pos, hi)
    if end > pos:
        hidden += (end - pos) - buf[pos:end].count(0)
    return hidden


def problems(path: str) -> List[str]:
    with open(path, "rb") as f:
        buf = f.read()
    bad: List[str] = []
    items: Dict[int, str] = {}
    locations: List[Tuple[int, int, int, int]] = []
    mdats: List[Tuple[int, int]] = []
    idat = None
    try:
        for typ, s, e in _boxes(buf, 0, len(buf)):
            if typ not in TOP_LEVEL:
                bad.append(tr("İzin listesi dışı HEIF kutusu: {box}", box=typ.decode("latin-1")))
            elif typ == b"mdat":
                mdats.append((s, e))
            elif typ == b"meta":
                for ctyp, cs, ce in _boxes(buf, s + 4, e):
                    if ctyp not in META_CHILDREN:
                        bad.append(tr("İzin listesi dışı HEIF kutusu: {box}", box="meta/" + ctyp.decode("latin-1")))
                    elif ctyp == b"iinf":
                        items = _items(buf, cs, ce)
                    elif ctyp == b"iloc":
                        locations = _locations(buf, cs)
                    elif ctyp == b"idat":
                        idat = (cs, ce)
                    elif ctyp == b"iprp":
                        for ptyp, ps, pe in _boxes(buf, cs, ce):
                            if ptyp == b"ipco":
                                for prop, _s, _e in _boxes(buf, ps, pe):
                                    if prop not in PROPERTIES:
                                        bad.append(tr("İzin listesi dışı HEIF özelliği: {prop}",
                                                      prop=prop.decode("latin-1")))
    except (ValueError, struct.error, IndexError) as e:
        return [tr("HEIF yapısı çözülemedi: {e}", e=e)]
    sizes: Dict[int, int] = {}
    for item_id, _method, _off, length in locations:
        sizes[item_id] = sizes.get(item_id, 0) + length
    for item_id, kind in sorted(items.items()):
        # ExifTool silinen XMP'nin (mime) kaydını boş bırakır; içinde veri olmayan öğe bilgi taşımaz
        if kind not in ITEM_TYPES and sizes.get(item_id, 0):
            bad.append(tr("İzin listesi dışı HEIF öğesi: {kind} (öğe {id})", kind=kind, id=item_id))
    for item_id in sorted({loc[0] for loc in locations} - set(items)):
        bad.append(tr("Türü bilinmeyen HEIF öğesine ait veri (öğe {id})", id=item_id))
    in_file = [(off, length) for _, method, off, length in locations if method == 0]
    in_idat = [(idat[0] + off, length) for _, method, off, length in locations if method == 1] if idat else []
    hidden = sum(_orphans(buf, s, e, in_file) for s, e in mdats)
    if idat:
        hidden += _orphans(buf, idat[0], idat[1], in_idat)
    if hidden:
        bad.append(tr("Hiçbir öğeye ait olmayan {n} bayt veri kaldı", n=hidden))
    return bad
