"""macOS galerilerinden sürükle-bırak.

Fotoğraflar, Safari, Chrome, Mail gibi uygulamalar sürüklenen dosyanın yolunu değil bir "dosya sözü"
(NSFilePromise) verir: dosyayı, bırakılan yerin gösterdiği klasöre kendileri yazar. Qt bunu
desteklemez; Fotoğraflar'dan gelen yol ise kütüphanenin içindeki küçük bir önizlemedir. Bu modül
orijinal dosyayı AppKit'in NSFilePromiseReceiver'ı ile geçici bir klasöre yazdırır.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable, List

log = logging.getLogger("metaclean.gui")

try:
    if sys.platform != "darwin":
        raise ImportError("yalnızca macOS")
    import AppKit
    import Foundation
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

LIBRARY_MARK = ".photoslibrary"
_queue = None  # tek arka plan kuyruğu; alıcıları ve geri çağrıları alım bitene kadar macOS tutar


def use_promises(paths: List[str], promises: bool) -> bool:
    """Dosya sözlerini ne zaman kullanmalı: hiç yerel yol yoksa ya da yol Fotoğraflar kütüphanesinin
    içindeki bir önizlemeyse. Finder'dan gelen gerçek yollar her zaman önceliklidir."""
    return promises and (not paths or any(LIBRARY_MARK in p for p in paths))


def _pasteboard():
    return AppKit.NSPasteboard.pasteboardWithName_(AppKit.NSPasteboardNameDrag)


def has_promises() -> bool:
    if not AVAILABLE:
        return False
    try:
        return bool(_pasteboard().canReadObjectForClasses_options_([AppKit.NSFilePromiseReceiver], None))
    except Exception:  # noqa: BLE001 - sürükleme pano okunamazsa sıradan sürükleme gibi davran
        log.exception("sürükleme panosu okunamadı")
        return False


def receive(dest_dir: str, on_file: Callable[[str], None], on_error: Callable[[str], None]) -> int:
    """Sürüklenen dosya sözlerini dest_dir'e yazdırır. Her dosya hazır olunca on_file(yol), olmazsa
    on_error(mesaj) çağrılır; ikisi de arka plan iş parçacığından gelir. Beklenen dosya sayısını döndürür.
    Bırakma (drop) olayının içinden çağrılmalı: sürükleme oturumu ancak o sırada açıktır."""
    if not AVAILABLE:
        return 0
    global _queue
    receivers = _pasteboard().readObjectsForClasses_options_([AppKit.NSFilePromiseReceiver], None) or []
    if not receivers:
        return 0
    if _queue is None:
        _queue = Foundation.NSOperationQueue.alloc().init()
    dest = Foundation.NSURL.fileURLWithPath_isDirectory_(dest_dir, True)

    def reader(url, error):
        if error is not None:
            on_error(str(error.localizedDescription()))
        else:
            on_file(str(url.path()))

    expected = 0
    for r in receivers:
        expected += max(1, len(r.fileTypes() or []))
        r.receivePromisedFilesAtDestination_options_operationQueue_reader_(dest, {}, _queue, reader)
    return expected
