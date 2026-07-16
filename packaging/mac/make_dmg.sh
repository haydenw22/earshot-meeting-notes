#!/bin/sh
# Package dist/Earshot.app into dist/Earshot.dmg (the human download: open,
# then drag Earshot to the Applications shortcut).
set -e
cd "$(dirname "$0")/../.."   # repo root

APP="dist/Earshot.app"
DMG="dist/Earshot.dmg"
[ -d "$APP" ] || { echo "error: $APP not found (build with pyinstaller packaging/earshot_mac.spec)"; exit 1; }

STAGE="$(mktemp -d /tmp/earshot-dmg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create -volname "Earshot" -srcfolder "$STAGE" -ov -format UDZO "$DMG" -quiet
echo "wrote $DMG ($(du -h "$DMG" | cut -f1 | tr -d ' '))"
