# macOS Release

VoiceFlow macOS releases target Apple Silicon and macOS 13+. Public artifacts must be Developer ID signed, hardened-runtime enabled, notarized, stapled, and uploaded as `VoiceFlow-<version>-macos-arm64.dmg`.

## Required Secrets

GitHub Actions release builds require:

- `MACOS_CERTIFICATE_P12_BASE64`: base64-encoded Developer ID Application certificate export.
- `MACOS_CERTIFICATE_PASSWORD`: password for the `.p12` export.
- `MACOS_SIGNING_IDENTITY`: Developer ID Application identity name used by `codesign`.
- `APPLE_ID`: Apple ID email used by `notarytool`.
- `APPLE_TEAM_ID`: Apple Developer Team ID.
- `APPLE_APP_SPECIFIC_PASSWORD`: app-specific password for notarization.

## Local Unsigned Smoke Build

```bash
pnpm run setup
pnpm run build:installer:macos:unsigned
```

The unsigned artifact is written to `dist/installer/VoiceFlow-<version>-macos-arm64-unsigned.dmg`.

## Local Signed Build

Import the Developer ID certificate into a local keychain, then export the notarization environment:

```bash
export MACOS_SIGNING_IDENTITY="Developer ID Application: Example, Inc. (TEAMID)"
export APPLE_ID="release@example.com"
export APPLE_TEAM_ID="TEAMID"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"

pnpm run build:installer:macos
```

The script patches `Info.plist`, verifies the executable is `arm64`, signs nested binaries and the outer app with `installer/macos-entitlements.plist`, creates the DMG, signs the DMG, submits it to Apple notarization, staples it, and runs Gatekeeper verification.

## CI Release Flow

The release workflow builds Linux, Windows, and macOS artifacts in parallel. The macOS job runs on the arm64 `macos-26` runner, imports the Developer ID certificate from secrets, runs `pnpm run build`, then calls `installer/build-macos.sh`. Tagged releases attach the signed `.dmg` alongside the Windows installer and Linux artifacts.

## Runtime Permissions

VoiceFlow declares:

- `NSMicrophoneUsageDescription` for microphone recording.
- `NSScreenCaptureUsageDescription` for ScreenCaptureKit system-audio capture.
- `NSInputMonitoringUsageDescription` for global hotkey listening.

Users may also need to grant Accessibility and Input Monitoring in System Settings for global hotkeys and automatic paste.

## References

- [Apple: ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit)
- [Apple: Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime)
- [Apple: Notarizing macOS software](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [GitHub: macOS arm64 hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
