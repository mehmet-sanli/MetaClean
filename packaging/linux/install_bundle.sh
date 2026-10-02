#!/bin/bash
# Linux paketini kurar (indirilen MetaClean klasörünün içinden çalıştırın): ./install_bundle.sh
# Yönetici izni gerekmez; her şey kullanıcının ev dizinine kurulur.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/metaclean"

rm -rf "$DEST"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/512x512/apps"
cp -R "$SRC/." "$DEST/"
ln -sf "$DEST/MetaClean" "$HOME/.local/bin/metaclean"
cp "$SRC/metaclean.png" "$HOME/.local/share/icons/hicolor/512x512/apps/metaclean.png"
sed "s|^Exec=.*|Exec=$DEST/MetaClean %F|" "$SRC/metaclean.desktop" > "$HOME/.local/share/applications/metaclean.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  cp "$HOME/.local/share/applications/metaclean.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/metaclean.desktop"
  gio set "$DESKTOP_DIR/metaclean.desktop" metadata::trusted true 2>/dev/null || true
fi
echo "MetaClean kuruldu. Uygulama menüsünden ya da masaüstündeki simgeden açabilirsiniz."
