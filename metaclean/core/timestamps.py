"""Değiştirilme, erişim ve oluşturulma zamanlarını ayarlama ve geri okuyup doğrulama.

Windows (NTFS) : os.utime oluşturulma zamanına dokunmaz -> SetFileTime (kernel32, ctypes).
                 Ayrıca "tunneling" yüzünden replace sonrası dosya eski oluşturulma tarihini
                 miras alabilir; bu çağrı o yüzden şart.
macOS (APFS)   : oluşturulma zamanı setattrlist(ATTR_CMN_CRTIME) ile.
Linux          : oluşturulma (birth) zamanı kullanıcı alanından ayarlanamaz.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from datetime import datetime
from typing import List, Optional

from ..i18n import tr

TOLERANCE = 2.0  # FAT/exFAT 2 saniyelik çözünürlük


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _set_birth_windows(path: str, ts: float) -> None:
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    v = int(ts * 10_000_000) + 116444736000000000  # 1601-01-01'den beri 100 ns
    ft = FILETIME(v & 0xFFFFFFFF, v >> 32)
    h = k32.CreateFileW(path, 0x100, 0x7, None, 3, 0x02000000, None)  # FILE_WRITE_ATTRIBUTES, OPEN_EXISTING
    if h in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not k32.SetFileTime(h, ctypes.byref(ft), ctypes.byref(ft), ctypes.byref(ft)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(h)


def _set_birth_macos(path: str, ts: float) -> None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    class AttrList(ctypes.Structure):
        _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                    ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                    ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32), ("forkattr", ctypes.c_uint32)]

    class Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    attrs = AttrList(5, 0, 0x00000200, 0, 0, 0, 0)  # ATTR_BIT_MAP_COUNT, ATTR_CMN_CRTIME
    spec = Timespec(int(ts), int((ts % 1) * 1e9))
    if libc.setattrlist(os.fsencode(path), ctypes.byref(attrs), ctypes.byref(spec), ctypes.sizeof(spec), 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def birth_time(path: str) -> Optional[float]:
    st = os.stat(path)
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    if os.name == "nt":
        return st.st_ctime  # Windows'ta eski Python'larda ctime = oluşturulma
    return None


def apply(path: str) -> List[str]:
    """Üç damgayı da şu ana (kaydetme anı) çeker, geri okuyup doğrular. Orijinalin tarihleri kopyaya taşınmaz.
    Dosyayı en son adımda çağırın: sonraki her okuma erişim zamanını oynatabilir."""
    ts = time.time()
    notes: List[str] = []
    birth_ok: Optional[bool] = None
    try:
        # Oluşturulma önce: macOS, değiştirilme oluşturulmadan eskiyse oluşturulmayı geri çeker
        if os.name == "nt":
            _set_birth_windows(path, ts)
            birth_ok = True
        elif sys.platform == "darwin":
            _set_birth_macos(path, ts)
            birth_ok = True
    except OSError as e:
        notes.append(tr("✗ Oluşturulma zamanı ayarlanamadı: {e}", e=e))
        birth_ok = False
    try:
        os.utime(path, (ts, ts))
    except OSError as e:
        # Dosya zaten kaydedildi; damga ayarlanamadı diye kayıt başarısız sayılmasın (doğrulama ✗ gösterir)
        notes.append(tr("✗ Zaman damgaları ayarlanamadı: {e}", e=e))

    st = os.stat(path)
    for label, value in ((tr("Değiştirilme"), st.st_mtime), (tr("Erişim"), st.st_atime)):
        ok = abs(value - ts) <= TOLERANCE
        notes.append(f"{'✓' if ok else '✗'} {label}: {_fmt(value)}")
    b = birth_time(path)
    if birth_ok is None:
        notes.append(tr("• Oluşturulma: Linux'ta kullanıcı alanından ayarlanamaz; dosyanın gerçek oluşturulma "
                        "anı (geçici dosyanın yaratıldığı an) kalır.")
                     + (tr(" Okunan: {t}", t=_fmt(b)) if b else ""))
        notes.append(tr("• Linux ctime oluşturulma değil, inode'un son değişim zamanıdır; onu çekirdek yazar."))
    elif b is not None:
        ok = abs(b - ts) <= TOLERANCE
        notes.append(f"{'✓' if ok else '✗'} {tr('Oluşturulma')}: {_fmt(b)}")
    return notes
