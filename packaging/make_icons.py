"""logo.svg'den platform simgelerini üretir: .png (Linux), .icns (macOS).

Kullanım: python packaging/make_icons.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metaclean", "gui", "assets")


def render(renderer: QSvgRenderer, px: int, path: str) -> None:
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    img.save(path)


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 - QPainter için gerekli
    r = QSvgRenderer(os.path.join(ASSETS, "logo.svg"))
    render(r, 512, os.path.join(ASSETS, "logo-512.png"))
    render(r, 256, os.path.join(ASSETS, "logo-256.png"))

    if sys.platform == "darwin" and shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = os.path.join(tmp, "MetaClean.iconset")
            os.mkdir(iconset)
            for s in (16, 32, 128, 256, 512):
                render(r, s, os.path.join(iconset, f"icon_{s}x{s}.png"))
                render(r, s * 2, os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(ASSETS, "MetaClean.icns")], check=True)
    print("Simgeler:", sorted(os.listdir(ASSETS)))


if __name__ == "__main__":
    main()
