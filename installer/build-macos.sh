#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="${VERSION:-$(node -p "require('$PROJECT_ROOT/package.json').version")}"
APP_NAME="VoiceFlow"
BUNDLE_ID="io.github.infiniv.VoiceFlow"
MIN_MACOS="13.0"
DIST_DIR="$PROJECT_ROOT/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
OUTPUT_DIR="$DIST_DIR/installer"
ENTITLEMENTS="$SCRIPT_DIR/macos-entitlements.plist"
UNSIGNED="${MACOS_UNSIGNED:-0}"

for arg in "$@"; do
  case "$arg" in
    --unsigned) UNSIGNED=1 ;;
    *)
      echo "Unknown argument: $arg"
      exit 2
      ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: macOS packaging must run on macOS."
  exit 1
fi

if [ ! -d "$APP_BUNDLE" ]; then
  echo "ERROR: App bundle not found at $APP_BUNDLE"
  echo "Run 'pnpm run build' first."
  exit 1
fi

PLIST="$APP_BUNDLE/Contents/Info.plist"
PLISTBUDDY="/usr/libexec/PlistBuddy"

set_plist_string() {
  local key="$1"
  local value="$2"
  if "$PLISTBUDDY" -c "Print :$key" "$PLIST" >/dev/null 2>&1; then
    "$PLISTBUDDY" -c "Set :$key $value" "$PLIST"
  else
    "$PLISTBUDDY" -c "Add :$key string $value" "$PLIST"
  fi
}

require_env() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "ERROR: $name is required for signed macOS release builds."
    exit 1
  fi
}

echo "=== Preparing $APP_NAME.app for macOS $MIN_MACOS+ arm64 ==="
set_plist_string "CFBundleIdentifier" "$BUNDLE_ID"
set_plist_string "CFBundleShortVersionString" "$VERSION"
set_plist_string "CFBundleVersion" "$VERSION"
set_plist_string "LSMinimumSystemVersion" "$MIN_MACOS"
set_plist_string "NSMicrophoneUsageDescription" "VoiceFlow needs microphone access to record dictation and meetings."
set_plist_string "NSScreenCaptureUsageDescription" "VoiceFlow needs Screen Recording permission to capture system audio for Meeting Mode."
set_plist_string "NSInputMonitoringUsageDescription" "VoiceFlow needs Input Monitoring permission to listen for global hotkeys."

EXECUTABLE="$("$PLISTBUDDY" -c "Print :CFBundleExecutable" "$PLIST")"
BINARY="$APP_BUNDLE/Contents/MacOS/$EXECUTABLE"
if ! file "$BINARY" | grep -q "arm64"; then
  echo "ERROR: $BINARY is not arm64:"
  file "$BINARY"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
if [ "$UNSIGNED" = "1" ]; then
  DMG_NAME="$APP_NAME-$VERSION-macos-arm64-unsigned.dmg"
else
  DMG_NAME="$APP_NAME-$VERSION-macos-arm64.dmg"
fi
DMG_PATH="$OUTPUT_DIR/$DMG_NAME"
STAGING="$DIST_DIR/dmg-staging"
rm -rf "$STAGING" "$DMG_PATH"
trap 'rm -rf "$STAGING"' EXIT

if [ "$UNSIGNED" != "1" ]; then
  require_env MACOS_SIGNING_IDENTITY
  require_env APPLE_ID
  require_env APPLE_TEAM_ID
  require_env APPLE_APP_SPECIFIC_PASSWORD

  echo "=== Signing nested app contents ==="
  while IFS= read -r -d '' path; do
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$MACOS_SIGNING_IDENTITY" "$path"
  done < <(find "$APP_BUNDLE/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) -print0)

  while IFS= read -r -d '' framework; do
    codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" \
      --sign "$MACOS_SIGNING_IDENTITY" "$framework"
  done < <(find "$APP_BUNDLE/Contents" -type d -name "*.framework" -print0)

  echo "=== Signing app bundle ==="
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --sign "$MACOS_SIGNING_IDENTITY" "$APP_BUNDLE"

  codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
else
  echo "=== Unsigned local build mode ==="
fi

echo "=== Creating DMG ==="
mkdir -p "$STAGING"
cp -a "$APP_BUNDLE" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  -imagekey zlib-level=9 \
  "$DMG_PATH"

if [ "$UNSIGNED" != "1" ]; then
  echo "=== Signing DMG ==="
  codesign --force --timestamp --sign "$MACOS_SIGNING_IDENTITY" "$DMG_PATH"
  codesign --verify --verbose=2 "$DMG_PATH"

  echo "=== Notarizing DMG ==="
  xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" \
    --wait

  echo "=== Stapling DMG ==="
  xcrun stapler staple "$DMG_PATH"
  spctl -a -t open --context context:primary-signature -v "$DMG_PATH"
fi

DMG_SIZE=$(du -h "$DMG_PATH" | cut -f1)
echo "=== macOS artifact complete ==="
echo "DMG: $DMG_PATH ($DMG_SIZE)"
