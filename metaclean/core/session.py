"""Bir dosyanın uçtan uca akışı: incele -> temizle -> 3 kapı -> onay -> kopya kaydet / vazgeç.
Orijinal dosyaya hiçbir zaman yazılmaz; temiz sürüm her zaman ayrı bir kopyadır."""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from ..i18n import tr
from . import allowlist, detect, fsops, timestamps, tools
from .handlers.audio import FlacHandler, Mp3Handler
from .handlers.base import Context, Handler
from .handlers.images import HeifHandler, JpegHandler, PngHandler, WebpHandler
from .handlers.video import AvHandler
from .report import Gate, Report

_LIVE_WORKDIRS: Set[str] = set()


def cleanup_stale_workdirs(max_age: float = 24 * 3600) -> int:
    """Önceki bir çökmeden kalan geçici klasörleri siler; normalde kapanışta silinirler ama çökmede bu kod
    çalışmaz. Galeriden alınan kopyalar konum gibi bilgiler taşıyabilir. Yalnızca bu kullanıcının ve bir
    günden eski klasörlere dokunulur: aynı anda açık başka bir MetaClean'in işi bozulmasın."""
    root, now, removed = tempfile.gettempdir(), time.time(), 0
    for name in os.listdir(root):
        if not name.startswith("metaclean-"):
            continue
        path = os.path.join(root, name)
        try:
            st = os.lstat(path)
            if (not os.path.isdir(path) or os.path.islink(path) or now - st.st_mtime < max_age
                    or (hasattr(os, "getuid") and st.st_uid != os.getuid())):
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


class Unsupported(ValueError):
    """Dosya imzası desteklenen biçimlerden biri değil (meta verisi yine de okunur)."""


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


KIND_PREFIX = {"image": "foto", "video": "video", "audio": "ses"}


def neutral_name(folder: str, fmt: str) -> str:
    """Tarih ya da cihaz adı taşımayan ad (foto-1.jpg, video-2.mp4…). Ad hemen ayrılır: aynı anda
    kaydedilen iki dosya aynı adı seçip birbirini ezmesin."""
    prefix, ext = KIND_PREFIX[detect.kind(fmt)], detect.ext(fmt)
    n = 1
    while True:
        path = os.path.join(folder, f"{prefix}-{n}{ext}")
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            return path
        except FileExistsError:
            n += 1


class Job:
    def __init__(self, path: str):
        self.path = path
        self.real = os.path.realpath(path)
        self.report = Report(path=path)
        self.cancel = threading.Event()
        self.workdir: Optional[str] = None
        self.out: Optional[str] = None
        self.orig_sig: Optional[Tuple[int, str]] = None

    @property
    def ready(self) -> bool:
        return self.out is not None and self.report.ok

    # ------------------------------------------------------------ hazırlık
    def prepare(self, progress: Callable[[str], None] = lambda s: None,
                scan: Optional[Dict[str, object]] = None) -> Report:
        """scan: arayüzün önceden aldığı ExifTool taraması; verilirse ikinci kez taranmaz."""
        r = self.report
        try:
            if not os.path.isfile(self.real):
                raise ValueError(tr("Normal bir dosya değil"))
            if os.path.abspath(self.path) != self.real:
                r.warnings.append(tr("Sembolik bağ çözüldü; asıl dosya işlenecek: {path}", path=self.real))
            fmt = detect.detect(self.real)
            if not fmt:
                # Temizlenemese de meta veri gösterilebilir (PDF, Word, RAW…); yalnızca okunur
                if tools.find_tool("exiftool"):
                    progress(tr("Meta veri okunuyor (ExifTool)"))
                    r.scan = scan or tools.exiftool_scan(self.real, log=r.log, cancel=self.cancel)
                    r.found = allowlist.sensitive(r.scan)
                raise Unsupported(tr("Desteklenmeyen biçim: bu dosya temizlenemez (imzası desteklenen biçimlerden "
                                     "biri değil). Meta verisi yalnızca görüntülenebilir."))
            r.fmt = fmt
            for t in (("exiftool",) if detect.kind(fmt) == "image" else ("exiftool", "ffmpeg", "ffprobe")):
                tools.require(t)
            progress(tr("Orijinalin SHA-256 özeti alınıyor"))
            self.orig_sig = signature(self.real)
            # Sistemin geçici klasörü (yalnızca kullanıcıya açık): onay alınmadan kullanıcının
            # klasörlerine hiçbir şey yazılmaz
            try:
                self.workdir = tempfile.mkdtemp(prefix="metaclean-")
            except OSError as e:
                raise ValueError(tr("Geçici klasöre yazılamıyor ({e}).", e=e))
            _LIVE_WORKDIRS.add(self.workdir)
            def step(text: str) -> None:
                if self.cancel.is_set():
                    raise tools.Cancelled()
                progress(text)

            ctx = Context(self.real, self.workdir, fmt, r, self.cancel, step)

            step(tr("Meta veri taranıyor (ExifTool)"))
            r.scan = scan or tools.exiftool_scan(self.real, log=r.log, cancel=self.cancel)
            r.found = allowlist.sensitive(r.scan, fmt)
            handler = make_handler(fmt)
            step(tr("Temizleniyor"))
            out = handler.clean(ctx)
            integrity, equivalence = handler.verify(ctx, out)
            step(tr("Temizlik: yeniden taranıyor"))
            r.gates = [integrity, equivalence, self._cleanliness(handler, ctx, out)]
            if signature(self.real) != self.orig_sig:
                raise RuntimeError(tr("Orijinal dosya işlem sırasında değişti."))
            if r.ok:
                self.out = out
            else:
                self.discard()
        except tools.Cancelled:
            r.error, r.error_kind = tr("İptal edildi."), "cancelled"
            self.discard()
        except Exception as e:  # noqa: BLE001 - her hata raporda gösterilir, temp silinir
            r.error = f"{type(e).__name__}: {e}" if not isinstance(e, (ValueError, tools.ToolError)) else str(e)
            r.error_kind = ("unsupported" if isinstance(e, Unsupported)
                            else "missing_tool" if isinstance(e, tools.ToolMissing) else "")
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
                details.append(tr("Yapısal denetim: çıktıda yalnızca izin listesindeki bloklar var."))
        self.report.clean_scan = tools.exiftool_scan(out, log=ctx.log, cancel=ctx.cancel)
        remaining = allowlist.sensitive(self.report.clean_scan, ctx.fmt)
        self.report.remaining = remaining
        if remaining:
            ok = False
            details += [tr("Kalan alan: {key} = {value}", key=f.key, value=f.value) for f in remaining]
        else:
            details.append(tr("ExifTool yeniden taraması (-a -u -G1 -ee): izin listesi dışında alan yok."))
        return Gate(tr("Temizlik"), ok, details)

    # ------------------------------------------------------------ onay sonrası
    def save_copy(self, dest: str) -> List[str]:
        if not self.ready:
            raise RuntimeError(tr("Kaydedilecek doğrulanmış dosya yok."))
        dest_real = os.path.realpath(dest)
        if dest_real == self.real:
            raise RuntimeError(tr("Kopya orijinalin üzerine yazılamaz."))
        dest_dir = os.path.dirname(dest_real)
        if os.stat(dest_dir).st_dev == os.stat(self.workdir).st_dev:
            _fsync_file(self.out)
            out = self.out
            fsops.retry_on_lock(lambda: os.replace(out, dest_real), "Hedef dosya")
        else:
            shutil.copyfile(self.out, dest_real)
            _fsync_file(dest_real)
        _fsync_dir(dest_dir)
        self.out = None
        self.discard()
        return timestamps.apply(dest_real)

    def discard(self) -> None:
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            _LIVE_WORKDIRS.discard(self.workdir)
            self.workdir = None
        self.out = None
