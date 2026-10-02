#!/bin/bash
# MetaClean'i macOS'a KAYNAK KODDAN kurar (geliştirme ve deneme için). Kullanıcılar için hazır paket
# MetaClean-macOS-*.dmg'dir. Çalışan kopya ~/Library/Application Support/MetaClean altına, çift
# tıklanan uygulama ~/Applications/MetaClean (kaynak).app olarak kurulur; DMG'den kurulan
# /Applications/MetaClean.app'e dokunulmaz.
# Masaüstü macOS'ta korumalı bir klasör; yeni bir uygulama oradaki Python ortamını okuyamaz.
# Bu yüzden kod ve ortam korumasız bir yere kopyalanır. Kodu değiştirince betiği yeniden çalıştırın.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/MetaClean"
APP="$HOME/Applications/MetaClean (kaynak).app"
mkdir -p "$HOME/Applications"
VERSION="$(grep -o '__version__ = "[^"]*"' "$SRC/metaclean/__init__.py" | cut -d'"' -f2)"

echo "1/4 Kod kopyalanıyor → $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/metaclean"
cp -R "$SRC/metaclean" "$SRC/requirements.txt" "$DEST/"
find "$DEST/metaclean" -name __pycache__ -prune -exec rm -rf {} +

echo "2/4 Python ortamı hazırlanıyor"
if [ ! -x "$DEST/.venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install -q --disable-pip-version-check -r "$DEST/requirements.txt"

echo "3/4 Uygulama oluşturuluyor → $APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/MacOS/MetaClean" <<'EOF'
#!/bin/bash
DEST="$HOME/Library/Application Support/MetaClean"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p "$HOME/Library/Logs/MetaClean"
cd "$DEST" || exit 1
# Finder'dan açılan betik uygulamaları Apple Silicon'da Intel (Rosetta) modunda başlayabilir;
# o zaman arm64 için derlenmiş Pillow yüklenemez. Yerel mimariyi zorla.
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
  exec arch -arm64 "$DEST/.venv/bin/python" -m metaclean "$@" 2>>"$HOME/Library/Logs/MetaClean/stderr.log"
fi
exec "$DEST/.venv/bin/python" -m metaclean "$@" 2>>"$HOME/Library/Logs/MetaClean/stderr.log"
EOF
chmod +x "$APP/Contents/MacOS/MetaClean"
mkdir -p "$APP/Contents/Resources"
cp "$SRC/metaclean/gui/assets/MetaClean.icns" "$APP/Contents/Resources/MetaClean.icns"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>MetaClean (kaynak)</string>
  <key>CFBundleDisplayName</key><string>MetaClean (kaynak)</string>
  <key>CFBundleIdentifier</key><string>app.metaclean.source</string>
  <key>CFBundleExecutable</key><string>MetaClean</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>MetaClean</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
  <key>NSDesktopFolderUsageDescription</key><string>Temiz kopyaları masaüstündeki “Paylaşıma Hazır” klasörüne kaydetmek için.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Seçtiğiniz dosyaları okumak için.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>Seçtiğiniz dosyaları okumak için.</string>
</dict></plist>
EOF
touch "$APP"  # Finder simgeyi yenilesin
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" || true

echo "4/4 Araçlar denetleniyor"
for t in exiftool ffmpeg ffprobe; do
  if command -v "$t" >/dev/null || [ -x "/opt/homebrew/bin/$t" ]; then echo "  ✓ $t"; else echo "  ✗ $t eksik → brew install exiftool ffmpeg"; fi
done
echo "Kurulum tamam (MetaClean $VERSION). Uygulamalar > MetaClean (kaynak) ile açın."
