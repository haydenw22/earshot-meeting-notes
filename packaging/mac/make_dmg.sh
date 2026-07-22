#!/bin/sh
# Package dist/Earshot.app into dist/Earshot.dmg: the human download. Mounts
# to a styled drag-to-install window (app icon, arrow, Applications shortcut).
# The Finder styling is best effort: environments without Finder scripting
# (some CI runners) still produce a working plain-window DMG.
set -e
cd "$(dirname "$0")/../.."   # repo root

APP="dist/Earshot.app"
DMG="dist/Earshot.dmg"
RW="dist/Earshot-rw.dmg"
VOL="Earshot"
[ -d "$APP" ] || { echo "error: $APP not found (build with pyinstaller packaging/earshot_mac.spec)"; exit 1; }

STAGE="$(mktemp -d /tmp/earshot-dmg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
mkdir "$STAGE/.background"
cp packaging/mac/dmg-background.png "$STAGE/.background/background.png"

# A leftover mounted volume from an earlier run breaks the Finder scripting.
hdiutil detach "/Volumes/$VOL" -quiet 2>/dev/null || true

rm -f "$DMG" "$RW"
hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov -format UDRW "$RW" -quiet
MOUNT_OUT="$(hdiutil attach "$RW" -readwrite -noverify -noautoopen)"
DEV="$(printf '%s\n' "$MOUNT_OUT" | awk 'NR==1{print $1}')"

if osascript <<'OSA'
tell application "Finder"
    tell disk "Earshot"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {200, 120, 860, 542}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 104
        set background picture of viewOptions to file ".background:background.png"
        set position of item "Earshot.app" of container window to {165, 200}
        set position of item "Applications" of container window to {495, 200}
        close
        open
        delay 1
        close
    end tell
end tell
OSA
then
    echo "styled the DMG window"
else
    echo "warning: DMG styling skipped (Finder scripting unavailable); plain window"
fi
sync
hdiutil detach "$DEV" -quiet || hdiutil detach "$DEV" -force -quiet
hdiutil convert "$RW" -format UDZO -o "$DMG" -quiet
rm -f "$RW"
echo "wrote $DMG ($(du -h "$DMG" | cut -f1 | tr -d ' '))"
