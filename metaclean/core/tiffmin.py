"""EXIF (TIFF yapısı) için asgari okuyucu/yazıcı.

Yalnızca iki şey gerekiyor: mevcut EXIF'ten yön (Orientation) ve renk alanı
bilgisini okumak, ve içinde yalnızca Orientation bulunan yeni bir EXIF üretmek.
"""
from __future__ import annotations

import struct
from typing import Dict, Optional, Tuple

ORIENTATION = 0x0112
EXIF_IFD = 0x8769
COLOR_SPACE = 0xA001

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _strip_header(data: bytes) -> bytes:
    return data[6:] if data.startswith(b"Exif\x00\x00") else data


def _ifd(tiff: bytes, offset: int) -> Tuple[str, Dict[int, Tuple[int, int, bytes]]]:
    bo = "<" if tiff[:2] == b"II" else ">"
    n = struct.unpack(bo + "H", tiff[offset:offset + 2])[0]
    entries = {}
    for i in range(n):
        p = offset + 2 + 12 * i
        tag, typ, count = struct.unpack(bo + "HHI", tiff[p:p + 8])
        entries[tag] = (typ, count, tiff[p + 8:p + 12])
    return bo, entries


def _short(bo: str, entry) -> Optional[int]:
    typ, count, raw = entry
    if typ == 3 and count >= 1:
        return struct.unpack(bo + "H", raw[:2])[0]
    if typ == 4 and count >= 1:
        return struct.unpack(bo + "I", raw)[0]
    return None


def read_info(data: bytes) -> Dict[str, Optional[int]]:
    """{'orientation': n|None, 'color_space': n|None}. Bozuk veride None döner."""
    info: Dict[str, Optional[int]] = {"orientation": None, "color_space": None}
    try:
        tiff = _strip_header(data)
        if tiff[:4] not in (b"II*\x00", b"MM\x00*"):
            return info
        bo = "<" if tiff[:2] == b"II" else ">"
        ifd0 = struct.unpack(bo + "I", tiff[4:8])[0]
        bo, entries = _ifd(tiff, ifd0)
        if ORIENTATION in entries:
            info["orientation"] = _short(bo, entries[ORIENTATION])
        if EXIF_IFD in entries:
            sub = _short(bo, entries[EXIF_IFD])
            if sub:
                _, sub_entries = _ifd(tiff, sub)
                if COLOR_SPACE in sub_entries:
                    info["color_space"] = _short(bo, sub_entries[COLOR_SPACE])
    except (struct.error, IndexError):
        pass
    return info


def build_orientation_tiff(orientation: int) -> bytes:
    """IFD0'da tek girdi (Orientation) olan TIFF bloğu; başka hiçbir alan yok."""
    return (b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
            + struct.pack(">HHIHH", ORIENTATION, 3, 1, orientation, 0) + struct.pack(">I", 0))


def only_orientation(data: bytes) -> bool:
    """EXIF bloğu yalnızca Orientation içeriyor mu (temizlik kapısı için)."""
    try:
        tiff = _strip_header(data)
        bo = "<" if tiff[:2] == b"II" else ">"
        ifd0 = struct.unpack(bo + "I", tiff[4:8])[0]
        _, entries = _ifd(tiff, ifd0)
        n = struct.unpack(bo + "H", tiff[ifd0:ifd0 + 2])[0]
        next_ifd = struct.unpack(bo + "I", tiff[ifd0 + 2 + 12 * n:ifd0 + 6 + 12 * n])[0]
        return set(entries) == {ORIENTATION} and next_ifd == 0
    except (struct.error, IndexError):
        return False
