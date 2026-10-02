"""Her format işleyicisinin uyguladığı arayüz: temizle -> doğrula."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from ..report import Gate, Kept, Report


@dataclass
class Options:
    full_video_decode: bool = False   # videoda kare kare çözerek karşılaştır


@dataclass
class Context:
    src: str
    workdir: str
    fmt: str
    report: Report
    options: Options = field(default_factory=Options)
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[str], None] = lambda s: None

    @property
    def log(self) -> List[str]:
        return self.report.log

    def removed(self, text: str) -> None:
        self.report.removed.append(text)

    def kept(self, what: str, reason: str) -> None:
        self.report.kept.append(Kept(what, reason))

    def warn(self, text: str) -> None:
        self.report.warnings.append(text)

    def out_path(self, ext: str) -> str:
        return os.path.join(self.workdir, "temiz" + ext)


class Handler:
    def clean(self, ctx: Context) -> str:
        """Temiz dosyayı ctx.workdir içinde üretir, yolunu döndürür."""
        raise NotImplementedError

    def verify(self, ctx: Context, out: str) -> Tuple[Gate, Gate]:
        """(bütünlük, eşdeğerlik) kapıları."""
        raise NotImplementedError

    def structural(self, ctx: Context, out: str) -> Optional[List[str]]:
        """Çıktının yapısını yeniden ayrıştırıp izin listesi dışı blokları listeler.
        None: bu format için yapısal denetim yok (yalnızca ExifTool taraması)."""
        return None
