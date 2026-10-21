#!/bin/bash
# Typography Wall — installer for Linux (64-bit x86, Ubuntu 22.04 or newer and similar).
# Downloads the latest release from GitHub, installs it for the current user,
# adds it to the applications menu, and opens it.
#
# One-line install (paste into a terminal):
#   curl -fsSL https://raw.githubusercontent.com/bassamanees/TypographyWall/main/install_linux.sh | bash

set -euo pipefail

REPO="bassamanees/TypographyWall"
ASSET="TypographyWall-linux-x64.tar.gz"
URL="https://github.com/$REPO/releases/latest/download/$ASSET"
ICON_URL="https://raw.githubusercontent.com/$REPO/main/app_icon.png"

DEST="$HOME/.local/opt/TypographyWall"
BIN="$DEST/TypographyWall"
DESKTOP_FILE="$HOME/.local/share/applications/typography-wall.desktop"

echo "== Typography Wall installer =="

if [ "$(uname -m)" != "x86_64" ]; then
  echo "Sorry: this build is for 64-bit Intel/AMD PCs (x86_64); this machine is $(uname -m)."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading the latest version (about 150 MB)..."
curl -fL --progress-bar "$URL" -o "$TMP/$ASSET"

echo "Installing to $DEST ..."
mkdir -p "$DEST"
tar -xzf "$TMP/$ASSET" -C "$TMP"
mv -f "$TMP/TypographyWall" "$BIN"
chmod +x "$BIN"
curl -fsSL "$ICON_URL" -o "$DEST/icon.png" || true

mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Typography Wall
Comment=Interactive Mesopotamian typography wall
Exec="$BIN"
Icon=$DEST/icon.png
Terminal=false
Categories=Education;Graphics;
EOF
chmod +x "$DESKTOP_FILE"

# Hand tracking (MediaPipe) needs the system OpenGL ES library. Desktop systems
# normally have it; if not, say how to add it.
if ! ldconfig -p 2>/dev/null | grep -q "libGLESv2.so.2"; then
  echo
  echo "One system library is missing. Install it once with:"
  if command -v apt-get >/dev/null; then echo "  sudo apt install libgles2"
  elif command -v dnf >/dev/null;   then echo "  sudo dnf install libglvnd-gles"
  elif command -v pacman >/dev/null; then echo "  sudo pacman -S libglvnd"
  else echo "  (the package that provides libGLESv2.so.2 — usually 'libgles2' or 'libglvnd')"
  fi
  echo "Then open Typography Wall from your applications menu."
  exit 0
fi

echo "Done. Typography Wall is in your applications menu. Opening it now..."
nohup "$BIN" >/dev/null 2>&1 &
