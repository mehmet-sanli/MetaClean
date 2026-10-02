"""Dosya sistemi yardımcıları: kilitli dosyada yeniden deneme ve salt okunur denetimi."""
from __future__ import annotations

import os
import stat
import time
from typing import Callable


def is_readonly(path: str) -> bool:
    """Windows'ta salt okunur özniteliği, diğerlerinde yazma izni."""
    if os.name == "nt":
        return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
    return not os.access(path, os.W_OK)


def retry_on_lock(action: Callable[[], None], what: str, attempts: int = 12, delay: float = 0.25) -> None:
    """Antivirüs, bulut eşitleme (OneDrive, iCloud, Dropbox), arama dizinleyicisi ya da açık bir
    görüntüleyici yeni yazılan dosyayı kısa süre kilitleyebilir; PermissionError'da yeniden dene."""
    for i in range(attempts):
        try:
            action()
            return
        except PermissionError:
            if i == attempts - 1:
                raise PermissionError(
                    f"{what} başka bir program tarafından kullanılıyor (açık bir görüntüleyici, bulut "
                    "eşitleme ya da antivirüs olabilir). Kapatıp yeniden deneyin.") from None
            time.sleep(delay * (1 + i // 4))
