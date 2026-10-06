"""Arayüz dilini seçip uygular: ayardaki seçim ya da (otomatikte) sistemin tercih ettiği dil."""
from __future__ import annotations

import logging
import sys
from typing import List

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

from .. import i18n
from . import dialogs

log = logging.getLogger("metaclean")


def system_languages() -> List[str]:
    """Sistemin tercih ettiği diller, en önemlisi önde.
    macOS: Qt dili LANG ortam değişkeninden okur; Finder/Dock'tan açılan uygulamada LANG olmadığı için "C"
    görür. Bu yüzden önce macOS'un kendi "Tercih Edilen Diller" listesine bakılır."""
    langs: List[str] = []
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale
            langs += [str(code) for code in NSLocale.preferredLanguages()]
        except ImportError:
            pass
    langs += [code for code in QLocale.system().uiLanguages() if code not in ("C", "POSIX")]
    return langs


def apply(app) -> str:
    """Dili seçer, çeviriyi ve Qt'nin kendi düğmelerinin (Evet/Hayır, Aç…) çevirisini kurar."""
    lang = i18n.resolve(dialogs.saved_language(), system_languages())
    i18n.set_language(lang)
    old = getattr(app, "_metaclean_qt_translator", None)
    if old is not None:
        app.removeTranslator(old)
    qt = QTranslator(app)
    if qt.load(f"qtbase_{lang}", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt)
    app._metaclean_qt_translator = qt
    log.info("arayüz dili: %s", lang)
    return lang
