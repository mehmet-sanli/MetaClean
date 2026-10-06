"""Dosya sistemi yardımcıları: kilitli dosyada yeniden deneme."""
from __future__ import annotations

import time
from typing import Callable

from ..i18n import tr


def retry_on_lock(action: Callable[[], None], what: str, attempts: int = 12, delay: float = 0.25) -> None:
    """Antivirüs, bulut eşitleme (OneDrive, iCloud, Dropbox), arama dizinleyicisi ya da açık bir
    görüntüleyici yeni yazılan dosyayı kısa süre kilitleyebilir; PermissionError'da yeniden dene."""
    for i in range(attempts):
        try:
            action()
            return
        except PermissionError:
            if i == attempts - 1:
                raise PermissionError(tr(
                    "{what} başka bir program tarafından kullanılıyor (açık bir görüntüleyici, bulut "
                    "eşitleme ya da antivirüs olabilir). Kapatıp yeniden deneyin.", what=what)) from None
            time.sleep(delay * (1 + i // 4))
