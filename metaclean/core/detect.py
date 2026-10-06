"""Format tanıma: uzantıya değil dosya imzasına (magic bytes) bakılır."""
from __future__ import annotations

import struct
from typing import Optional

# format -> (tür, çıktı uzantısı, okunur ad)
FORMATS = {
    "jpeg": ("image", ".jpg", "JPEG"),
    "png": ("image", ".png", "PNG"),
    "webp": ("image", ".webp", "WebP"),
    "heif": ("image", ".heic", "HEIC/HEIF"),
    "avif": ("image", ".avif", "AVIF"),
    "mp3": ("audio", ".mp3", "MP3"),
    "flac": ("audio", ".flac", "FLAC"),
    "m4a": ("audio", ".m4a", "M4A"),
    "mp4": ("video", ".mp4", "MP4"),
    "mov": ("video", ".mov", "QuickTime MOV"),
    "mkv": ("video", ".mkv", "Matroska MKV"),
    "webm": ("video", ".webm", "WebM"),
}

_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
_AVIF_BRANDS = {b"avif", b"avis"}
_M4A_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
_MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1",
               b"dash", b"MSNV", b"XAVC", b"mmp4", b"f4v ", b"M4V ", b"M4VH", b"M4VP"}


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _is_mp3_frame(h: bytes) -> bool:
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return False
    version = (h[1] >> 3) & 3
    layer = (h[1] >> 1) & 3
    bitrate = h[2] >> 4
    rate = (h[2] >> 2) & 3
    return version != 1 and layer == 1 and bitrate not in (0, 15) and rate != 3


def _ebml_doctype(head: bytes) -> Optional[str]:
    i = head.find(b"\x42\x82")
    if i < 0 or i + 3 > len(head):
        return None
    first = head[i + 2]
    n = 1
    while n <= 8 and not (first & (0x80 >> (n - 1))):
        n += 1
    size = first & (0xFF >> n)
    for b in head[i + 3:i + 2 + n]:
        size = (size << 8) | b
    start = i + 2 + n
    return head[start:start + size].decode("ascii", "replace")


def detect(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        head = f.read(8192)
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        dt = _ebml_doctype(head[:128])
        return {"matroska": "mkv", "webm": "webm"}.get(dt or "")
    if head[4:8] == b"ftyp":
        size = struct.unpack(">I", head[:4])[0]
        major = head[8:12]
        compat = {head[i:i + 4] for i in range(16, min(size, len(head)), 4)}
        brands = {major} | compat
        if major in _AVIF_BRANDS or (major in _HEIF_BRANDS and brands & _AVIF_BRANDS):
            return "avif"
        if major in _HEIF_BRANDS:
            return "heif"
        if major == b"qt  ":
            return "mov"
        if major in _M4A_BRANDS:
            return "m4a"
        if major in _MP4_BRANDS:
            return "mp4"
        return None
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mov"  # ftyp'siz eski QuickTime
    # FLAC ve MP3 başında ID3v2 olabilir; kapak resmi gömülüyse etiket megabaytlarca sürer. Etiketler
    # dosyadaki gerçek konumlarından okunarak atlanır (her adım en az 10 bayt ilerler)
    off = 0
    with open(path, "rb") as f:
        while True:
            f.seek(off)
            tag = f.read(10)
            if len(tag) < 10 or tag[:3] != b"ID3":
                break
            off += _syncsafe(tag[6:10]) + 10 + (10 if tag[5] & 0x10 else 0)
        f.seek(off)
        start = f.read(4)
    if start == b"fLaC":
        return "flac"
    if _is_mp3_frame(start):
        return "mp3"
    return None


def kind(fmt: str) -> str:
    return FORMATS[fmt][0]


def ext(fmt: str) -> str:
    return FORMATS[fmt][1]


def label(fmt: str) -> str:
    return FORMATS[fmt][2]
