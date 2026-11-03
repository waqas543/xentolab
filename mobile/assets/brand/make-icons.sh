#!/usr/bin/env bash
# Regenerate every app icon from the SVG masters in this folder.
# Needs rsvg-convert and ImageMagick (brew install librsvg imagemagick). Run from anywhere:
#   mobile/assets/brand/make-icons.sh
set -euo pipefail
BRAND="$(cd "$(dirname "$0")" && pwd)"
MOBILE="$(cd "$BRAND/../.." && pwd)"
RES="$MOBILE/android/app/src/main/res"
IOS="$MOBILE/ios/Karobar/Images.xcassets/AppIcon.appiconset"
BG="#0B7A5C"

png() {          # png <svg> <size> <out>
  rsvg-convert -w "$2" -h "$2" "$BRAND/$1" -o "$3"
}
opaque() {       # iOS and the store reject icons with an alpha channel
  magick "$1" -background "$BG" -alpha remove -alpha off "$1"
}

# Android legacy launcher icons (Android 7.x; newer versions use the adaptive icon below)
for pair in mdpi:48 hdpi:72 xhdpi:96 xxhdpi:144 xxxhdpi:192; do
  density=${pair%%:*}; size=${pair##*:}
  png xentolab-icon-rounded.svg "$size" "$RES/mipmap-$density/ic_launcher.png"
  png xentolab-icon-round.svg "$size" "$RES/mipmap-$density/ic_launcher_round.png"
done

# iOS
for spec in 40:icon-20@2x 60:icon-20@3x 58:icon-29@2x 87:icon-29@3x 80:icon-40@2x 120:icon-40@3x \
            120:icon-60@2x 180:icon-60@3x 1024:icon-1024; do
  size=${spec%%:*}; name=${spec##*:}
  png xentolab-icon-square.svg "$size" "$IOS/$name.png"
  opaque "$IOS/$name.png"
done

# Store listing and in-app logo
mkdir -p "$BRAND/store" "$MOBILE/src/assets"
png xentolab-icon-square.svg 512 "$BRAND/store/play-store-icon-512.png"
opaque "$BRAND/store/play-store-icon-512.png"
png xentolab-icon-square.svg 1024 "$BRAND/store/app-store-icon-1024.png"
opaque "$BRAND/store/app-store-icon-1024.png"
png xentolab-icon-rounded.svg 40 "$MOBILE/src/assets/logo.png"
png xentolab-icon-rounded.svg 80 "$MOBILE/src/assets/logo@2x.png"
png xentolab-icon-rounded.svg 120 "$MOBILE/src/assets/logo@3x.png"

echo "Icons written. The Android adaptive icon (res/drawable/ic_launcher_*.xml) is vector; update it by hand if the mark changes."
