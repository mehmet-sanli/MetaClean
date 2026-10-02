"""Uygulama kaydı (log). Gizlilik uygulaması olduğu için kayda hiçbir dosya yolu ya da dosya adı
yazılmaz: yollar yalnızca uzantısı bırakılarak "<dosya>.jpg" biçimine indirilir.

Kayıt yeri işletim sisteminin kayıt klasörüdür; dosya 512 KB'ı geçince eskisi atılır.
  macOS   ~/Library/Logs/MetaClean/metaclean.log
  Windows %LOCALAPPDATA%\\MetaClean\\Logs\\metaclean.log
  Linux   $XDG_STATE_HOME/metaclean/metaclean.log  (varsayılan ~/.local/state)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys

# Unix yolu (/…) ya da Windows yolu (C:\… veya \\sunucu\…); boşluk içeren adlar da tek parça yakalanır
_PATH = re.compile(r"""(?:[A-Za-z]:\\|\\\\|/(?=[^\s/]))[^'"\]\[<>|\n,]*?(\.[A-Za-z0-9]{1,5})?(?=['"\]\[,\n]|\s(?:->|hata=|rapor=)|$)""")


def log_dir() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/MetaClean")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MetaClean", "Logs")
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "metaclean")


def redact(text: str) -> str:
    return _PATH.sub(lambda m: "<dosya>" + (m.group(1) or ""), text)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def setup() -> str:
    folder = log_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "metaclean.log")
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter())
    root = logging.getLogger("metaclean")
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
    return path
