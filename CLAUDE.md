# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VoiceFlow is a cross-platform voice-to-text paste utility built with Pyloid (Python desktop framework using PySide6/Qt WebEngine) and React. Users hold a hotkey to record audio, release to transcribe using faster-whisper, and the text is automatically pasted at the cursor. Supports Windows, Linux (Wayland/X11), and macOS.

As of v1.6.0, VoiceFlow also ships a **Meeting Mode** — long-form recording with mic + system-loopback capture, async transcription, and LLM-generated summaries. This is a separate feature surface from the push-to-talk paste flow, kept self-contained under `services/recording/` so it can evolve (or be extracted) without touching PTT code.

## Commands

```bash
# Initial setup (installs both Node and Python dependencies)
pnpm run setup

# Development mode (runs Vite frontend + Pyloid backend concurrently)
pnpm run dev

# Development with hot-reload for Python changes
pnpm run dev:watch

# Build desktop application
pnpm run build

# Build platform installers (run on each target OS)
pnpm run build:installer          # Windows (.exe via Inno Setup)
pnpm run build:installer:linux    # Linux (.tar.gz + .AppImage)
pnpm run build:installer:macos    # macOS (.dmg)

# Run Python tests (fast suite — excludes test_transcription, which downloads a model)
pnpm run test
pnpm run test:all                 # includes test_transcription

# Run single test file
uv run -p .venv pytest src-pyloid/tests/test_transcription.py -v

# Everything CI runs (lint + typecheck + tests + version consistency)
pnpm run check

# Version bump — updates all five version files (package.json, voiceflow.iss,
# pyproject.toml, constants.ts, uv.lock); never edit them by hand
pnpm run version:bump X.Y.Z
pnpm run version:check

# Run frontend only (for UI development)
pnpm run vite

# Lint / typecheck frontend
pnpm run lint
pnpm run typecheck
```

CI (`.github/workflows/ci.yml`) runs lint, typecheck, version check, and the fast
test suite on every PR and push to main. The release workflow
(`.github/workflows/release.yml`) additionally refuses tags that don't match
package.json and verifies the Linux artifact's audio-library layout
(bundled libasound/libportaudio removed, PyAV's av.libs copy intact).

## Architecture

### Backend (src-pyloid/)

Python backend using Pyloid framework with PySide6:

- **main.py** - Application entry point. Creates Pyloid app, tray icon, main dashboard window, and recording popup window. Sets up UI callbacks connecting backend events to popup state changes.
- **server.py** - RPC server using `PyloidRPC`. Exposes PTT methods (`get_settings`, `update_settings`, `get_history`, etc.) plus the full Meeting Mode surface (`meetings.list_audio_sources`, `meetings.start`, `meetings.pause`, `meetings.resume`, `meetings.stop`, `meetings.transcribe`, `meetings.summarize`, `meetings.get_llm_config`, `meetings.test_llm_connection`, etc.) that frontend calls via `pyloid-js` RPC.
- **app_controller.py** - Singleton controller orchestrating all services. Handles hotkey activate/deactivate flow: start recording -> stop recording -> transcribe -> paste at cursor -> save to history. Also constructs and owns the `MeetingsController` (exposed as `controller.meetings`) and runs an unfinished-recording recovery sweep on startup.

**Services (src-pyloid/services/):**
- `audio.py` - Microphone recording using sounddevice, streams amplitude for visualizer
- `transcription.py` - faster-whisper model loading and transcription
- `hotkey.py` - Global hotkey listener using keyboard library
- `clipboard.py` - Clipboard operations and paste-at-cursor using pyautogui
- `settings.py` - Settings management with defaults
- `database.py` - SQLite database for settings and history (stored at ~/.VoiceFlow/VoiceFlow.db)
- `logger.py` - Domain-based logging with hybrid format `[timestamp] [LEVEL] [domain] message | {json}`. Supports domains: model, audio, hotkey, settings, database, clipboard, window, plus Meeting-Mode domains (recording, transcribe, summary, llm). Configured with 100MB log rotation.
- `model_manager.py` - Whisper model download/cache management using huggingface_hub. Provides download progress tracking (percent, speed, ETA), cancellation via CancelToken, daemon thread execution, and `clear_cache()` to delete only VoiceFlow's faster-whisper models.

**Meeting Mode services (src-pyloid/services/recording/):**
Self-contained per `docs/adr/0003-meeting-mode-isolation.md`; do not call these from the PTT path and vice versa.
- `controller.py` - `MeetingsController` — the feature's facade. All RPC handlers go through this object. Emits `recording-state`, `meetings.transcribe-progress`, and `meetings.summarize-progress` events to the frontend via the emitter installed by `main.py`.
- `recorder.py` - Long-form recorder with pause/resume, segmented WAV writing, and clock tracking. Sources are fixed at `start()` and cannot change mid-recording.
- `audio_source.py` - Enumerates available mic + loopback devices for the UI device picker.
- `loopback_linux.py` / `loopback_pulse.py` / `loopback_windows.py` - Platform-specific system-audio capture (PulseAudio/PipeWire on Linux, WASAPI loopback on Windows).
- `clock.py` - Monotonic recording clock that survives pause/resume.
- `llm.py` - LLM client with preset + custom-endpoint support; used by summary/title generation.
- `summary.py` / `title.py` - LLM-driven summary and auto-title generation for finished recordings.
- `secrets.py` - API-key storage for LLM providers (kept out of the main settings table).
- `export.py` - Exports a recording's transcript/summary to text formats.
- `recovery.py` - On startup, sweeps recordings left in `recording` / `paused` state from a previous (crashed) session and rolls them forward.
- `audio_scheme.py` / `audio_scheme_handler.py` - Custom Qt `audio://` URL scheme so the WebEngine `<audio>` element can stream recording WAVs from disk without a server.

### Frontend (src/)

React 18 + TypeScript + Vite frontend:

- **App.tsx** - Hash-based routing between `/popup`, `/onboarding`, and `/dashboard`. Checks model cache on startup and shows recovery modal if model is missing.
- **lib/api.ts** - RPC wrapper using `pyloid-js` to call Python backend methods. Includes model management APIs (`getModelInfo`, `startModelDownload`, `cancelModelDownload`) plus the full `recordings*` / meetings RPC surface.
- **lib/types.ts** - TypeScript interfaces for Settings, HistoryEntry, Stats, Options, ModelInfo, DownloadProgress, plus meeting types: `Recording`, `RecordingSegment`, `RecorderState`, and LLM config types.
- **pages/** - Popup (recording indicator), Onboarding (includes model download step), Dashboard. Dashboard uses React Router for sub-routes: `history`, `meetings`, `meetings/record`, `meetings/:id`, `settings`.
- **components/** - Feature components plus shadcn/ui components in `components/ui/`
  - `ModelDownloadProgress.tsx` - Download progress UI with progress bar, speed, ETA, and retry support
  - `ModelDownloadModal.tsx` - Dialog wrapper for model downloads triggered from settings
  - `ModelRecoveryModal.tsx` - Startup modal for missing model recovery
  - `meetings/` - Meeting Mode UI: `MeetingsListPage`, `MeetingRecorderPage`, `MeetingDetailPage`, `MeetingImportDialog`, `MeetingRecorderContext` (cross-route recorder state), `AudioPlayer`, `LevelMeter`, `StatusLine`, `TranscriptView`, `SummaryView`, `RetranscribeDialog`, `LLMSettingsSection`, `MeetingsSettingsSection`.

### Frontend-Backend Communication

The frontend uses `pyloid-js` RPC to call Python methods:
```typescript
import { rpc } from "pyloid-js";
const settings = await rpc.call("get_settings");
```

Backend sends events to popup window via:
```python
popup_window.invoke('popup-state', {'state': 'recording'})
```

### Recording Flow

1. User holds hotkey (configurable, default Ctrl+Win)
2. `HotkeyService.on_activate` -> `AppController._handle_hotkey_activate` -> `AudioService.start_recording`
3. Popup transitions to "recording" state, shows amplitude visualizer
4. User releases hotkey
5. `AudioService.stop_recording` returns audio numpy array
6. `TranscriptionService.transcribe` runs faster-whisper
7. `ClipboardService.paste_at_cursor` pastes text
8. History saved to database
9. Popup returns to "idle" state

### Qt Threading Pattern

The `keyboard` library runs hotkey callbacks in a separate thread, but Qt requires UI operations on the main thread. The solution uses Qt signals/slots:

1. `ThreadSafeSignals` class in `main.py` defines signals (recording_started, recording_stopped, etc.)
2. Callback functions from `AppController` emit signals instead of directly updating UI
3. Signals connect to slot functions with `Qt.QueuedConnection` to ensure they run on the main thread
4. Slot functions safely update popup state and window properties

### Popup Window Transparency

For transparent popup windows on Windows:
- Set `transparent=True` when creating the window
- Call `qwindow.setAttribute(Qt.WA_TranslucentBackground, True)` on the Qt window
- Set `webview.page().setBackgroundColor(QColor(0, 0, 0, 0))` before loading URL
- Re-apply `WA_TranslucentBackground` after any `setWindowFlags()` call

### Model Download Flow

1. App/Onboarding/Settings triggers model download via `startModelDownload(modelName)`
2. `ModelManager` creates daemon thread, starts `huggingface_hub.snapshot_download()`
3. Custom tqdm class captures progress, sends updates via callback (throttled to 10/sec)
4. Frontend receives `download-progress` events with percent, speed, ETA
5. User can cancel via `cancelModelDownload()` which sets CancelToken
6. On completion, model is cached in huggingface cache directory
7. Turbo model uses `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (same as faster-whisper internal mapping)

### Meeting Mode (long-form recording)

Separate from the PTT paste flow. Entrypoint: `controller.meetings` (`MeetingsController`). See `docs/adr/0001-stereo-channel-layout-for-recordings.md` for the on-disk audio layout decision.

1. UI calls `meetings.list_audio_sources()` to populate the device picker (mic + loopback).
2. `meetings.start(mic_device_id, loopback_device_id)` opens up to two simultaneous capture streams. Sources are fixed at start — they cannot be added/removed mid-recording.
3. Audio is written to a WAV file under `~/.VoiceFlow/recordings/`:
   - Two active sources → **stereo 16 kHz PCM16**, mic on **L**, loopback on **R** (kept separate on purpose; enables future speaker diarization with no ML — see ADR 0001).
   - One active source → mono 16 kHz PCM16.
4. `meetings.pause()` / `meetings.resume()` use a monotonic `Clock` to track real recording time; segments are stitched into one logical recording.
5. `meetings.stop()` finalizes the WAV and persists metadata. Recording rows live in the same SQLite DB but in their own table.
6. Transcription is **async and on-demand**: `meetings.transcribe(id)` runs faster-whisper in a daemon thread and emits `meetings.transcribe-progress` events. Long jobs do not block the RPC channel (see fix `dc04d29`).
7. After transcription, `meetings.summarize(id, prompt)` calls the configured LLM provider (preset or custom endpoint) to produce an AI summary, and `title.py` auto-generates a title. LLM config and API keys live in `services/recording/llm.py` + `secrets.py`, not in the main `settings` table.
8. Audio playback in the detail page uses a custom Qt `audio://` URL scheme (`audio_scheme.py`) so the WebEngine can stream the WAV directly without an HTTP server.
9. On startup, `recovery.py` rolls forward any recordings left in `recording` / `paused` state from a crashed previous session.

**Platform quirks**:
- Linux loopback uses PulseAudio/PipeWire monitor sources (`loopback_pulse.py` / `loopback_linux.py`).
- Windows loopback uses WASAPI. Must open the loopback stream at the device's native channel count (`max_output_channels`) — opening at a forced channel count fails on many devices (fixes `96b0b73`, `13d45be`).
- Pyloid validates window IDs on every RPC roundtrip from a background thread; long-running meeting RPCs work around this (see `135fdd7`).

## Key Patterns

- **Singleton controller**: `get_controller()` returns singleton `AppController` instance
- **UI callbacks**: Backend notifies frontend of state changes via callbacks set in `set_ui_callbacks()`
- **Thread-safe signals**: Qt signals with `QueuedConnection` marshal UI updates from background threads to main thread
- **Background threads**: Model loading, downloads, and transcription run in daemon threads
- **Domain logging**: All services use `get_logger(domain)` for structured logging with domains like `model`, `audio`, `hotkey`, etc.
- **Custom hotkeys**: Supports modifier-only combos (e.g., Ctrl+Win) and standard combos (e.g., Ctrl+R). Frontend captures keys, backend validates and registers.
- **Path alias**: Frontend uses `@/` for `src/` imports (configured in tsconfig.json and vite.config.ts)
- **Lazy pyautogui**: `ClipboardService` lazy-loads pyautogui via `_get_pyautogui()` to avoid `mouseinfo`'s `sys.exit()` when tkinter is missing in bundled builds
- **Reduced effects on Linux**: `App.tsx` adds `reduced-effects` class on Linux to disable `backdrop-filter`, animated orbs, and noise texture that cause sluggish UI under Qt WebEngine software rendering. Popup route is excluded (needs transparency).
- **Popup visibility**: `showPopup` setting controls the floating recording indicator. Backend uses `_popup_visible` flag and `on_popup_visibility_changed()` callback. When hidden, `resize_popup()` is skipped to prevent re-showing.

### Linux-Specific

- **evdev hotkeys**: Linux uses `evdev` for keyboard input instead of `keyboard` library (Wayland-compatible)
- **Wayland clipboard**: Uses `wl-copy` for clipboard, `wtype`/`dotool`/`ydotool` for paste keystroke, falls back to pyautogui via XWayland
- **Qt WebEngine flags**: `QTWEBENGINE_ENABLE_LINUX_ACCESSIBILITY=0` and Chromium flags (`--use-gl=egl`, `--disable-gpu-sandbox`) set in `main.py` to improve rendering performance
- **Hyprland rules**: `_setup_hyprland_window_rules()` in `main.py` configures popup as floating, pinned, no-focus via `hyprctl`
- **Multi-monitor**: `get_active_monitor_info()` re-detects cursor monitor on each recording start

## Testing

Python tests use pytest and are in `src-pyloid/tests/`. Test files include:
- `test_logger.py` - Logger infrastructure tests
- `test_model_manager.py` - Model download/cache management tests
- `test_transcription.py` - Transcription service tests (slow, downloads model on first run)
- `test_audio.py`, `test_hotkey.py`, `test_clipboard.py`, `test_settings.py`, `test_app_controller.py`

## UI Components

Uses shadcn/ui (New York style) with Tailwind CSS v4. Add components via:
```bash
npx shadcn@latest add <component>
```

## Git Commit Guidelines

- **Never add co-author lines** to commit messages
- Keep commit messages concise and descriptive
- Use conventional commit prefixes: `fix:`, `feat:`, `update:`, `refactor:`, etc.
- Follow the release guide at `docs/plans/release-guide.md` for creating releases
