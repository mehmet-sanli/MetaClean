"""Bir dosyanın uçtan uca akışı: incele -> temizle -> 3 kapı -> onay -> kaydet/kopya/iptal."""
from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import tempfile
import threading
from typing import Callable, List, Optional, Set, Tuple

from . import allowlist, detect, timestamps, tools
from .handlers.audio import FlacHandler, Mp3Handler
from .handlers.base import Context, Handler, Options
from .handlers.images import HeifHandler, JpegHandler, PngHandler, WebpHandler
from .handlers.video import AvHandler
from .report import Gate, Report

_LIVE_WORKDIRS: Set[str] = set()


@atexit.register
def _cleanup_workdirs() -> None:
    for d in list(_LIVE_WORKDIRS):
        shutil.rmtree(d, ignore_errors=True)


def make_handler(fmt: str) -> Handler:
    if fmt == "jpeg":
        return JpegHandler()
    if fmt == "png":
        return PngHandler()
    if fmt == "webp":
        return WebpHandler()
    if fmt in ("heif", "avif"):
        return HeifHandler(detect.ext(fmt))
    if fmt == "mp3":
        return Mp3Handler()
    if fmt == "flac":
        return FlacHandler()
    return AvHandler(fmt)


def signature(path: str) -> Tuple[int, str]:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return os.path.getsize(path), h.hexdigest()


def _fsync_file(path: str) -> None:
    with open(path, "r+b") as f:
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: str) -> None:
    if os.name == "nt":
        return  # Windows'ta klasör fsync'i yok
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def suggest_copy_name(real: str, fmt: str) -> str:
    """Kopya için nötr ad. IMG_20260815_142233 gibi tarih taşıyan adlar önerilmez."""
    folder, name = os.path.split(real)
    stem = os.path.splitext(name)[0]
    base = "temiz" if re.search(r"\d{6,}", stem) else f"{stem}-temiz"
    ext = detect.ext(fmt)
    cand, n = os.path.join(folder, base + ext), 2
    while os.path.exists(cand):
        cand, n = os.path.join(folder, f"{base}-{n}{ext}"), n + 1
    return cand


class Job:
    def __init__(self, path: str, options: Optional[Options] = None):
        self.path = path
        self.real = os.path.realpath(path)
        self.options = options or Options()
        self.report = Report(path=path)
        self.cancel = threading.Event()
        self.workdir: Optional[str] = None
        self.out: Optional[str] = None
        self.orig_sig: Optional[Tuple[int, str]] = None

    @property
    def ready(self) -> bool:
        return self.out is not None and self.report.ok

    # ------------------------------------------------------------ hazırlık
    def prepare(self, progress: Callable[[str], None] = lambda s: None) -> Report:
        r = self.report
        try:
            if not os.path.isfile(self.real):
                raise ValueError("Normal bir dosya değil")
            if os.path.abspath(self.path) != self.real:
                r.warnings.append(f"Sembolik bağ çözüldü; asıl dosya işlenecek: {self.real}")
            if os.stat(self.real).st_nlink > 1:
                r.warnings.append("Bu dosyanın başka sabit bağları (hard link) var. 'Kaydet' yalnızca bu adı "
                                  "değiştirir; diğer adlar eski, kirli içeriği göstermeye devam eder.")
            fmt = detect.detect(self.real)
            if not fmt:
                # Temizlenemese de meta veri gösterilebilir (PDF, Word, RAW…); yalnızca okunur
                if tools.find_tool("exiftool"):
                    progress("Meta veri okunuyor (ExifTool)")
                    r.scan = tools.exiftool_scan(self.real, log=r.log, cancel=self.cancel)
                    r.found = allowlist.sensitive(r.scan)
                raise ValueError("Desteklenmeyen biçim: bu dosya temizlenemez (imzası desteklenen biçimlerden biri değil). "
                                 "Meta verisi yalnızca görüntülenebilir; 'Meta veri' sekmesine bakın.")
            r.fmt = fmt
            for t in (("exiftool",) if detect.kind(fmt) == "image" else ("exiftool", "ffmpeg", "ffprobe")):
                tools.require(t)
            progress("Orijinalin SHA-256 özeti alınıyor")
            self.orig_sig = signature(self.real)
            folder = os.path.dirname(self.real)
            try:
                self.workdir = tempfile.mkdtemp(prefix=".metaclean-", dir=folder)
            except OSError as e:
                raise ValueError(f"Dosyanın klasörüne yazılamıyor ({e}). Geçici dosya orijinalle aynı "
                                 "klasörde olmalı: atomik değiştirme yalnızca aynı disk bölümünde mümkün.")
            _LIVE_WORKDIRS.add(self.workdir)
            def step(text: str) -> None:
                if self.cancel.is_set():
                    raise tools.Cancelled()
                progress(text)

            ctx = Context(self.real, self.workdir, fmt, r, self.options, self.cancel, step)

            step("Meta veri taranıyor (ExifTool)")
            r.scan = tools.exiftool_scan(self.real, log=r.log, cancel=self.cancel)
            r.found = allowlist.sensitive(r.scan, fmt)
            handler = make_handler(fmt)
            step("Temizleniyor")
            out = handler.clean(ctx)
            integrity, equivalence = handler.verify(ctx, out)
            step("Temizlik: yeniden taranıyor")
            r.gates = [integrity, equivalence, self._cleanliness(handler, ctx, out)]
            if signature(self.real) != self.orig_sig:
                raise RuntimeError("Orijinal dosya işlem sırasında değişti.")
            if r.ok:
                self.out = out
            else:
                self.discard()
        except tools.Cancelled:
            r.error = "İptal edildi."
            self.discard()
        except Exception as e:  # noqa: BLE001 - her hata raporda gösterilir, temp silinir
            r.error = f"{type(e).__name__}: {e}" if not isinstance(e, (ValueError, tools.ToolError)) else str(e)
            self.discard()
        return r

    def _cleanliness(self, handler: Handler, ctx: Context, out: str) -> Gate:
        details: List[str] = []
        ok = True
        bad = handler.structural(ctx, out)
        if bad is not None:
            if bad:
                ok = False
                details += bad
            else:
                details.append("Yapısal denetim: çıktıda yalnızca izin listesindeki bloklar var.")
        self.report.clean_scan = tools.exiftool_scan(out, log=ctx.log, cancel=ctx.cancel)
        remaining = allowlist.sensitive(self.report.clean_scan, ctx.fmt)
        self.report.remaining = remaining
        if remaining:
            ok = False
            details += [f"Kalan alan: {f.key} = {f.value}" for f in remaining]
        else:
            details.append("ExifTool yeniden taraması (-a -u -G1 -ee): izin listesi dışında alan yok.")
        return Gate("Temizlik", ok, details)

    # ------------------------------------------------------------ onay sonrası
    def save_replace(self, when: Optional[float] = None) -> List[str]:
        if not self.ready:
            raise RuntimeError("Kaydedilecek doğrulanmış dosya yok.")
        if signature(self.real) != self.orig_sig:
            self.discard()
            raise RuntimeError("Orijinal dosya onay beklenirken değişti; kaydetme iptal edildi.")
        if os.name == "nt" and not os.access(self.real, os.W_OK):
            raise RuntimeError("Orijinal salt okunur; Windows salt okunur dosyanın yerine yazmaya izin vermez.")
        _fsync_file(self.out)
        shutil.copymode(self.real, self.out)  # yalnızca izinler; copy2/copystat eski damgaları taşır
        os.replace(self.out, self.real)
        _fsync_dir(os.path.dirname(self.real))
        self.out = None
        self.discard()
        return timestamps.apply(self.real, when)

    def save_copy(self, dest: str, when: Optional[float] = None) -> List[str]:
        if not self.ready:
            raise RuntimeError("Kaydedilecek doğrulanmış dosya yok.")
        dest_real = os.path.realpath(dest)
        if dest_real == self.real:
            raise RuntimeError("Kopya orijinalin üzerine yazılamaz; bunun için 'Kaydet'i kullanın.")
        dest_dir = os.path.dirname(dest_real)
        if os.stat(dest_dir).st_dev == os.stat(self.workdir).st_dev:
            _fsync_file(self.out)
            os.replace(self.out, dest_real)
        else:
            shutil.copyfile(self.out, dest_real)
            _fsync_file(dest_real)
        _fsync_dir(dest_dir)
        self.out = None
        self.discard()
        return timestamps.apply(dest_real, when)

    def discard(self) -> None:
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            _LIVE_WORKDIRS.discard(self.workdir)
            self.workdir = None
        self.out = None
