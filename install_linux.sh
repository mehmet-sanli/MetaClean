#!/bin/bash
# MetaClean'i Linux'a kaynak koddan kurar: ./install_linux.sh
# Kurulum yeri: ~/.local/share/metaclean · uygulama menüsüne ve masaüstüne simge ekler.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/metaclean"

echo "1/4 Sistem paketleri denetleniyor"
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c "import venv, ensurepip" 2>/dev/null || missing+=(python3-venv)
command -v exiftool >/dev/null || missing+=(exiftool)
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
if [ ${#missing[@]} -gt 0 ]; then
  echo "  Eksik: ${missing[*]}"
  if command -v apt-get >/dev/null; then
    echo "  sudo apt-get install -y python3-venv libimage-exiftool-perl ffmpeg libxcb-cursor0"
    sudo apt-get install -y python3-venv libimage-exiftool-perl ffmpeg libxcb-cursor0
  elif command -v dnf >/dev/null; then
    sudo dnf install -y python3 perl-Image-ExifTool ffmpeg xcb-util-cursor
  elif command -v pacman >/dev/null; then
    sudo pacman -S --needed python perl-image-exiftool ffmpeg xcb-util-cursor
  else
    echo "  Bu paketleri dağıtımınızın paket yöneticisiyle kurun, sonra betiği yeniden çalıştırın."; exit 1
  fi
fi

echo "2/4 Kod kopyalanıyor → $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/metaclean"
cp -R "$SRC/metaclean" "$SRC/requirements.txt" "$DEST/"

echo "3/4 Python ortamı hazırlanıyor"
[ -x "$DEST/.venv/bin/python" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --disable-pip-version-check -r "$DEST/requirements.txt"

echo "4/4 Simge ve kısayol"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/512x512/apps"
cat > "$HOME/.local/bin/metaclean" <<EOF
#!/bin/bash
cd "$DEST" && exec "$DEST/.venv/bin/python" -m metaclean "\$@"
EOF
chmod +x "$HOME/.local/bin/metaclean"
cp "$SRC/metaclean/gui/assets/logo-512.png" "$HOME/.local/share/icons/hicolor/512x512/apps/metaclean.png"
sed "s|^Exec=.*|Exec=$HOME/.local/bin/metaclean %F|" "$SRC/packaging/linux/metaclean.desktop" \
  > "$HOME/.local/share/applications/metaclean.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  cp "$HOME/.local/share/applications/metaclean.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/metaclean.desktop"
  gio set "$DESKTOP_DIR/metaclean.desktop" metadata::trusted true 2>/dev/null || true
fi
"$DEST/.venv/bin/python" -m metaclean --selftest | tail -1
echo "Kurulum tamam. Uygulama menüsünden ya da masaüstündeki MetaClean simgesinden açın."
