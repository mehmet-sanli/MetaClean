# PyInstaller tarifi: macOS ve Linux için tek klasörlük MetaClean paketi.
# Önce: python packaging/fetch_tools.py   Sonra: pyinstaller packaging/metaclean.spec
import os
import sys

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "metaclean", "gui", "assets")

# packaging/bin altındaki ExifTool ve FFmpeg paketin bin/ klasörüne gider (core/tools.py orada arar)
tools = []
for folder, _, files in os.walk(os.path.join(HERE, "bin")):
    rel = os.path.relpath(folder, HERE)
    tools += [(os.path.join(folder, f), rel) for f in files]

a = Analysis(
    [os.path.join(HERE, "run_metaclean.py")],
    pathex=[ROOT],
    datas=[(ASSETS, "metaclean/gui/assets")] + tools,
    hiddenimports=["PySide6.QtSvg", "pillow_heif", "mutagen"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MetaClean", console=False,
          icon=os.path.join(ASSETS, "MetaClean.icns") if sys.platform == "darwin" else None)
coll = COLLECT(exe, a.binaries, a.datas, name="MetaClean")

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="MetaClean.app", icon=os.path.join(ASSETS, "MetaClean.icns"),
        bundle_identifier="app.metaclean",
        info_plist={
            "CFBundleDisplayName": "MetaClean",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSDesktopFolderUsageDescription": "Temiz kopyaları masaüstündeki “Paylaşıma Hazır” klasörüne kaydetmek için.",
            "NSDocumentsFolderUsageDescription": "Seçtiğiniz dosyaları okumak için.",
            "NSDownloadsFolderUsageDescription": "Seçtiğiniz dosyaları okumak için.",
        },
    )
