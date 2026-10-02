"""JPEG'deki ikincil görüntüler: HDR kazanç haritası (korunur) ve diğerleri (silinir).

Ultra HDR (Android, Google) ve ISO 21496-1 fotoğrafları iki görüntü taşır: herkesin gördüğü SDR ana
görüntü ve HDR ekranda parlaklığı geri kuran küçük bir "kazanç haritası". Kazanç haritası ana
görüntünün EOI'sinden sonra durur; yeri MPF (APP2) dizininde ve Ultra HDR'de ayrıca XMP'deki
GContainer dizininde yazılıdır. Silinirse HDR ekranda fotoğraf mat görünür; bu yüzden korunur, ama
kendi EXIF/XMP'si temizlenir ve yalnızca HDR görüntüleme için gereken parametreler kalır.
Derinlik haritası, önizleme gibi diğer ikincil görüntüler kişisel bilgi taşıyabildiği için silinir.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"
ISO_SIG = b"urn:iso:std:iso:ts:21496:-1\x00"
MPF_SIG = b"MPF\x00"

NS_X = "adobe:ns:meta/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_HDRGM = "http://ns.adobe.com/hdr-gain-map/1.0/"
NS_CONTAINER = "http://ns.google.com/photos/1.0/container/"
NS_ITEM = "http://ns.google.com/photos/1.0/container/item/"
NS_APDI = "http://ns.apple.com/pixeldatainfo/1.0/"
NS_APPLE_GAIN = "http://ns.apple.com/HDRGainMap/1.0/"
APPLE_GAIN_TYPE = "urn:com:apple:photo:2020:aux:hdrgainmap"

# Kazanç haritasının XMP'sinde yalnızca bu ad alanları kalır (hepsi görüntüleme parametresi)
GAIN_MAP_NAMESPACES = {NS_HDRGM, NS_APDI, NS_APPLE_GAIN}
PRIMARY_NAMESPACES = {NS_HDRGM, NS_CONTAINER, NS_ITEM}

for _p, _u in (("x", NS_X), ("rdf", NS_RDF), ("hdrgm", NS_HDRGM), ("Container", NS_CONTAINER),
               ("Item", NS_ITEM), ("apdi", NS_APDI), ("HDRGainMap", NS_APPLE_GAIN)):
    ET.register_namespace(_p, _u)


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _parse_xmp(payload: bytes) -> Optional[ET.Element]:
    try:
        xml = payload[len(XMP_SIG):].decode("utf-8", "replace").strip("\x00 \n\r\t")
        start = xml.find("<x:xmpmeta")
        if start < 0:
            start = xml.find("<rdf:RDF")
        end = max(xml.rfind("</x:xmpmeta>") + len("</x:xmpmeta>"), xml.rfind("</rdf:RDF>") + len("</rdf:RDF>"))
        return ET.fromstring(xml[start:end])
    except (ET.ParseError, ValueError):
        return None


def _wrap(description: ET.Element) -> bytes:
    meta = ET.Element(f"{{{NS_X}}}xmpmeta")
    rdf = ET.SubElement(meta, f"{{{NS_RDF}}}RDF")
    rdf.append(description)
    return XMP_SIG + ET.tostring(meta, encoding="utf-8", xml_declaration=False)


def sanitize_xmp(payload: bytes, allowed: set) -> Optional[bytes]:
    """XMP'den yalnızca izinli ad alanlarındaki öznitelik ve öğeleri taşıyan yeni bir XMP üretir.
    İzinli hiçbir şey yoksa None (XMP tamamen atılır)."""
    root = _parse_xmp(payload)
    if root is None:
        return None
    desc = ET.Element(f"{{{NS_RDF}}}Description", {f"{{{NS_RDF}}}about": ""})
    found = False
    for d in root.iter(f"{{{NS_RDF}}}Description"):
        for k, v in d.attrib.items():
            if _ns(k) in allowed:
                desc.set(k, v)
                found = True
        for child in d:
            if _ns(child.tag) in allowed:
                desc.append(child)
                found = True
    return _wrap(desc) if found else None


def xmp_namespaces(payload: bytes) -> set:
    """XMP'de kullanılan ad alanları (temizlik kapısı için)."""
    root = _parse_xmp(payload)
    if root is None:
        return {"<okunamadı>"}
    used = set()
    for el in root.iter():
        used.add(_ns(el.tag))
        used.update(_ns(k) for k in el.attrib)
    return used - {"", NS_X, NS_RDF}


def primary_xmp(gain_map_lengths: List[int]) -> bytes:
    """Ultra HDR ana görüntüsü için asgari XMP: hdrgm sürümü ve GContainer dizini."""
    desc = ET.Element(f"{{{NS_RDF}}}Description", {f"{{{NS_RDF}}}about": "", f"{{{NS_HDRGM}}}Version": "1.0"})
    directory = ET.SubElement(desc, f"{{{NS_CONTAINER}}}Directory")
    seq = ET.SubElement(directory, f"{{{NS_RDF}}}Seq")
    items = [{"Semantic": "Primary", "Mime": "image/jpeg"}]
    items += [{"Semantic": "GainMap", "Mime": "image/jpeg", "Length": str(n)} for n in gain_map_lengths]
    for item in items:
        li = ET.SubElement(seq, f"{{{NS_RDF}}}li", {f"{{{NS_RDF}}}parseType": "Resource"})
        ET.SubElement(li, f"{{{NS_CONTAINER}}}Item", {f"{{{NS_ITEM}}}{k}": v for k, v in item.items()})
    return _wrap(desc)


def has_hdrgm(payload: bytes) -> bool:
    return payload.startswith(XMP_SIG) and NS_HDRGM.encode() in payload


def is_gain_map(segments) -> bool:
    """İkincil görüntünün kendi segmentlerine bakarak kazanç haritası olup olmadığını söyler."""
    for m, p in segments:
        if p is None:
            continue
        if m == 0xE2 and p.startswith(ISO_SIG):
            return True
        if m == 0xE1 and p.startswith(XMP_SIG) and (NS_HDRGM.encode() in p or APPLE_GAIN_TYPE.encode() in p):
            return True
    return False


# ---------------------------------------------------------------- MPF (CIPA DC-007)

PRIMARY_ATTR = 0x20030000  # temsil eden görüntü, temel JPEG ana görüntü


def mpf_tiff_offset(data: bytes) -> Optional[int]:
    """Dosyada MPF TIFF başlığının mutlak konumu (MPF ofsetleri buna göredir)."""
    i = data.find(b"\xff\xe2")
    while 0 <= i < len(data) - 8:
        if data[i + 4:i + 8] == MPF_SIG:
            return i + 8
        i = data.find(b"\xff\xe2", i + 2)
    return None


def parse_mpf(payload: bytes) -> List[Tuple[int, int, int]]:
    """[(öznitelik, boyut, ofset)] — ofset MPF TIFF başlığına göre (ana görüntü için 0)."""
    tiff = payload[len(MPF_SIG):]
    bo = "<" if tiff[:2] == b"II" else ">"
    ifd = struct.unpack(bo + "I", tiff[4:8])[0]
    n = struct.unpack(bo + "H", tiff[ifd:ifd + 2])[0]
    entries = []
    for k in range(n):
        p = ifd + 2 + 12 * k
        tag, typ, count = struct.unpack(bo + "HHI", tiff[p:p + 8])
        if tag == 0xB002:
            off = struct.unpack(bo + "I", tiff[p + 8:p + 12])[0]
            for j in range(count // 16):
                attr, size, offset = struct.unpack(bo + "III", tiff[off + 16 * j:off + 16 * j + 12])
                entries.append((attr, size, offset))
    return entries


def build_mpf(entries: List[Tuple[int, int, int]]) -> bytes:
    """MPFVersion, NumberOfImages ve MPEntry'den oluşan asgari MPF (büyük uçlu)."""
    n = len(entries)
    ifd = struct.pack(">H", 3)
    ifd += struct.pack(">HHI4s", 0xB000, 7, 4, b"0100")
    ifd += struct.pack(">HHII", 0xB001, 4, 1, n)
    ifd += struct.pack(">HHII", 0xB002, 7, 16 * n, 8 + 2 + 3 * 12 + 4)
    ifd += struct.pack(">I", 0)
    table = b"".join(struct.pack(">IIIHH", a, s, o, 0, 0) for a, s, o in entries)
    return MPF_SIG + b"MM\x00\x2a" + struct.pack(">I", 8) + ifd + table
