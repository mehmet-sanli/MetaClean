"""MetaClean'i başlatır: python -m metaclean [dosya ...]"""
import logging
import os
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox



LOG_PATH = os.path.join(os.path.expanduser("~"), "MetaClean.log")


def _excepthook(kind, value, tb):
    # Qt yuvalarındaki yakalanmamış hatalar sessizce kaybolmasın
    text = "".join(traceback.format_exception(kind, value, tb))
    logging.getLogger("metaclean").error("Yakalanmamış hata:\n%s", text)
    if QApplication.instance():
        QMessageBox.critical(None, "Beklenmeyen hata", text[-2000:])


def selftest() -> int:
    """Pencere açmadan uçtan uca sınama: araçlar, çözücüler ve gerçek bir temizlik.
    Derlenen paketlerin her işletim sisteminde çalıştığını kanıtlamak için."""
    import shutil
    import tempfile

    from PIL import Image

    from .core import tools
    from .core.session import Job

    lines, ok = [], True
    for t in tools.TOOLS:
        path = tools.find_tool(t)
        lines.append(f"{'OK ' if path else 'YOK'} {t}: {tools.tool_version(t) if path else '-'} ({path})")
        ok &= bool(path)
    tmp = tempfile.mkdtemp(prefix="metaclean-selftest-")
    try:
        src = os.path.join(tmp, "deneme.jpg")
        im = Image.new("RGB", (64, 48), (200, 80, 40))
        ex = im.getexif()
        ex[0x010F], ex[0x0110] = "Kamera", "Model"
        ex.get_ifd(0x8825)[1] = "N"
        im.save(src, exif=ex.tobytes(), comment=b"gizli")
        r = Job(src).prepare()
        lines.append(f"{'OK ' if r.ok else 'HATA'} JPEG temizliği: {[g.name + ('+' if g.passed else '-') for g in r.gates]} {r.error or ''}")
        ok &= r.ok
        try:
            import pillow_heif  # noqa: F401
            lines.append("OK  pillow-heif")
        except ImportError:
            lines.append("YOK pillow-heif (HEIC çözülemez)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines.append("SONUÇ: " + ("BAŞARILI" if ok else "BAŞARISIZ"))
    text = "\n".join(lines)
    print(text, flush=True)
    with open(os.path.join(tempfile.gettempdir(), "metaclean-selftest.txt"), "w", encoding="utf-8") as f:
        f.write(text)  # Windows'ta pencereli uygulamanın konsol çıktısı görünmez
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("metaclean").info("başladı, python %s, cwd %s", sys.version.split()[0], os.getcwd())
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("MetaClean")
    app.setOrganizationName("MetaClean")
    from PySide6.QtGui import QIcon
    assets = os.path.join(os.path.dirname(__file__), "gui", "assets")
    app.setWindowIcon(QIcon(os.path.join(assets, "logo.svg")))
    if "--gelismis" in sys.argv:
        sys.argv.remove("--gelismis")
        from .gui.main_window import MainWindow
        win = MainWindow()
    else:
        from .gui.simple_window import SimpleWindow
        win = SimpleWindow()
    win.show()
    if len(sys.argv) > 1:
        win.add_paths(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
