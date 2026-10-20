#!/bin/bash
# Typography Wall — installer for Mac (Apple Silicon: M1 and newer).
# Downloads the latest release from GitHub, installs it in Applications, and opens it.
#
# One-line install (paste into Terminal):
#   curl -fsSL https://raw.githubusercontent.com/bassamanees/TypographyWall/main/install_mac.command | bash

set -euo pipefail

REPO="bassamanees/TypographyWall"
ASSET="TypographyWall-macos-apple-silicon.zip"
URL="https://github.com/$REPO/releases/latest/download/$ASSET"
APP="TypographyWall.app"

echo "== Typography Wall installer =="

if [ "$(uname -m)" != "arm64" ]; then
  echo "Sorry: this Mac has an Intel processor. The hand-tracking library (MediaPipe)"
  echo "only supports Apple Silicon Macs (M1 and newer)."
  exit 1
fi

# Applications for everyone if writable, otherwise the user's own Applications folder
DEST="/Applications"
if [ ! -w "$DEST" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading the latest version..."
curl -fL --progress-bar "$URL" -o "$TMP/$ASSET"

echo "Installing to $DEST ..."
ditto -x -k "$TMP/$ASSET" "$TMP/unzipped"
rm -rf "$DEST/$APP"
ditto "$TMP/unzipped/$APP" "$DEST/$APP"

# The app is not signed with a paid Apple certificate; remove the download quarantine
# so macOS lets it open without the "cannot be opened" block.
xattr -dr com.apple.quarantine "$DEST/$APP" 2>/dev/null || true

echo "Done. Opening Typography Wall (allow camera access when macOS asks)."
open "$DEST/$APP"
