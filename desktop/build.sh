#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
OUT="${1:-$HERE/../dist}"
APP="$OUT/TMCRA 学习悬浮窗.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
SDK="${TMCRA_SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}"
ARCH="${TMCRA_ARCH:-$(uname -m)}"
export SDKROOT="$SDK"
xcrun swiftc -swift-version 5 -O -target "$ARCH-apple-macos13.0" -sdk "$SDK" \
  "$HERE/Sources/PanelState.swift" "$HERE/Sources/main.swift" -framework AppKit -framework WebKit -framework AVFoundation \
  -o "$APP/Contents/MacOS/TMCRALearningCompanion"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
cp "$HERE/Resources/"* "$APP/Contents/Resources/"
codesign --force --sign - "$APP" >/dev/null
codesign --verify --deep --strict "$APP"
printf 'BUILT_APP=%s\n' "$APP"
