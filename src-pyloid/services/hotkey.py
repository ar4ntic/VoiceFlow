import sys
import os
import subprocess
from typing import Callable, Optional
import threading
import time
from pathlib import Path
from services.logger import get_logger

log = get_logger("hotkey")

# Platform detection
IS_LINUX = sys.platform.startswith('linux')
IS_DARWIN = sys.platform == 'darwin'
IS_WIN32 = sys.platform == 'win32'

# Canonical modifier order for consistent hotkey strings
MODIFIER_ORDER = ['ctrl', 'alt', 'shift', 'win']
VALID_MODIFIERS = {'ctrl', 'alt', 'shift', 'win', 'windows', 'left windows', 'right windows'}


def normalize_hotkey(hotkey: str) -> str:
    """Normalize a hotkey string to a canonical format.

    Ensures consistent ordering: modifiers first (in MODIFIER_ORDER), then main keys.
    Also normalizes key names (e.g., 'windows' -> 'win').

    Example: 'r+win+ctrl' -> 'ctrl+win+r'
    """
    if not hotkey or not hotkey.strip():
        return hotkey

    parts = [p.strip().lower() for p in hotkey.split('+')]

    # Normalize key names
    normalized_parts = []
    for p in parts:
        if p in ('windows', 'left windows', 'right windows'):
            normalized_parts.append('win')
        elif p == 'control':
            normalized_parts.append('ctrl')
        else:
            normalized_parts.append(p)

    # Separate modifiers and main keys
    modifiers = []
    main_keys = []
    for p in normalized_parts:
        if p in {'ctrl', 'alt', 'shift', 'win'}:
            if p not in modifiers:  # Avoid duplicates
                modifiers.append(p)
        else:
            if p not in main_keys:  # Avoid duplicates
                main_keys.append(p)

    # Sort modifiers by canonical order
    modifiers.sort(key=lambda m: MODIFIER_ORDER.index(m) if m in MODIFIER_ORDER else 99)

    # Sort main keys alphabetically for consistency
    main_keys.sort()

    return '+'.join(modifiers + main_keys)


# Validation utilities
def validate_hotkey(hotkey: str) -> tuple[bool, str]:
    """Validate a hotkey string format.

    Returns (is_valid, error_message).

    Allows both:
    - modifier+key combos (e.g., "ctrl+r")
    - multiple modifiers (e.g., "ctrl+win") - these work with the keyboard library
    """
    if not hotkey or not hotkey.strip():
        return False, "Hotkey cannot be empty"

    parts = [p.strip().lower() for p in hotkey.split('+')]

    if len(parts) < 2:
        return False, "Hotkey must have at least two keys"

    # Normalize key names for validation
    normalized_parts = []
    for p in parts:
        if p in ('windows', 'left windows', 'right windows'):
            normalized_parts.append('win')
        elif p == 'control':
            normalized_parts.append('ctrl')
        else:
            normalized_parts.append(p)

    # Check that we have at least one modifier
    modifiers = [p for p in normalized_parts if p in {'ctrl', 'alt', 'shift', 'win'}]
    if not modifiers:
        return False, "Hotkey must include at least one modifier (Ctrl, Alt, Shift, or Win)"

    # Allow either:
    # 1. At least one modifier + at least one non-modifier key (e.g., "ctrl+r")
    # 2. At least two modifiers (e.g., "ctrl+win") - these are valid hotkeys
    main_keys = [p for p in normalized_parts if p not in {'ctrl', 'alt', 'shift', 'win'}]
    if not main_keys and len(modifiers) < 2:
        return False, "Hotkey must have at least two keys (modifier+key or multiple modifiers)"

    return True, ""


def are_hotkeys_conflicting(hotkey1: str, hotkey2: str) -> bool:
    """Check if two hotkeys conflict (are identical when normalized)."""
    if not hotkey1 or not hotkey2:
        return False

    return normalize_hotkey(hotkey1) == normalize_hotkey(hotkey2)


def _is_source_run() -> bool:
    return not bool(getattr(sys, "frozen", False))


def _macos_source_host_name() -> Optional[str]:
    if not IS_DARWIN:
        return None

    host_names = [
        ("cursor", "Cursor"),
        ("visual studio code", "Visual Studio Code"),
        ("code helper", "Visual Studio Code"),
        ("codex", "Codex"),
        ("terminal", "Terminal"),
        ("iterm", "iTerm"),
        ("warp", "Warp"),
        ("ghostty", "Ghostty"),
    ]

    pid = os.getpid()
    seen: set[int] = set()
    for _ in range(16):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)

        try:
            output = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "ppid=", "-o", "args="],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            break
        if not output:
            break

        parts = output.split(maxsplit=1)
        if not parts:
            break

        args = parts[1] if len(parts) > 1 else ""
        normalized = args.lower()
        for needle, label in host_names:
            if needle in normalized:
                return label

        try:
            pid = int(parts[0])
        except ValueError:
            break

    return None


def _macos_permission_message(permission: str) -> str:
    executable = Path(sys.executable).name or "the launcher process"

    if _is_source_run():
        host = _macos_source_host_name()
        if host:
            if permission == "accessibility":
                return (
                    f"VoiceFlow is running from {host}, so macOS grants "
                    f"Accessibility to {host} for this dev session. Turn on "
                    f"{host} in System Settings, not the VoiceFlow.app entries, "
                    "then click Check again."
                )
            return (
                f"VoiceFlow is running from {host}, so macOS grants Input "
                f"Monitoring to {host} for this dev session. Turn on {host} "
                "in System Settings, not the VoiceFlow.app entries, then "
                "click Check again."
            )

        if permission == "accessibility":
            return (
                "VoiceFlow is running from source, so macOS grants Accessibility "
                f"to {executable}, Terminal, or your editor instead of VoiceFlow.app. "
                "Enable the item macOS shows for the running process, then restart "
                "the dev session if the banner remains."
            )
        return (
            "VoiceFlow is running from source, so macOS grants Input Monitoring "
            f"to {executable}, Terminal, or your editor instead of VoiceFlow.app. "
            "Enable the item macOS shows for the running process, then restart "
            "the dev session if the banner remains."
        )

    if permission == "accessibility":
        return (
            "VoiceFlow needs Accessibility permission before global hotkeys and "
            "automatic paste can work. If VoiceFlow is already enabled, quit and "
            "reopen VoiceFlow so macOS applies the change."
        )
    return (
        "VoiceFlow needs Input Monitoring permission before it can listen for "
        "global hotkeys. If VoiceFlow is already enabled, quit and reopen "
        "VoiceFlow so macOS applies the change."
    )


# ============================================================================
# evdev helpers for Linux - reads directly from /dev/input, bypasses Wayland
# ============================================================================
if IS_LINUX:
    import evdev
    import select

    # Map evdev key codes to VoiceFlow key names
    _EVDEV_MODIFIER_CODES = {
        evdev.ecodes.KEY_LEFTCTRL: 'ctrl',
        evdev.ecodes.KEY_RIGHTCTRL: 'ctrl',
        evdev.ecodes.KEY_LEFTALT: 'alt',
        evdev.ecodes.KEY_RIGHTALT: 'alt',
        evdev.ecodes.KEY_LEFTSHIFT: 'shift',
        evdev.ecodes.KEY_RIGHTSHIFT: 'shift',
        evdev.ecodes.KEY_LEFTMETA: 'win',
        evdev.ecodes.KEY_RIGHTMETA: 'win',
    }

    # Map evdev key codes to regular key names
    _EVDEV_KEY_MAP = {
        evdev.ecodes.KEY_SPACE: 'space',
        evdev.ecodes.KEY_ENTER: 'enter',
        evdev.ecodes.KEY_TAB: 'tab',
        evdev.ecodes.KEY_ESC: 'esc',
        evdev.ecodes.KEY_BACKSPACE: 'backspace',
        evdev.ecodes.KEY_DELETE: 'delete',
        evdev.ecodes.KEY_UP: 'up',
        evdev.ecodes.KEY_DOWN: 'down',
        evdev.ecodes.KEY_LEFT: 'left',
        evdev.ecodes.KEY_RIGHT: 'right',
    }
    # Add letter keys
    for i in range(26):
        _EVDEV_KEY_MAP[getattr(evdev.ecodes, f'KEY_{chr(65+i)}')] = chr(97+i)
    # Add number keys
    for i in range(10):
        code = getattr(evdev.ecodes, f'KEY_{i}', None)
        if code:
            _EVDEV_KEY_MAP[code] = str(i)
    # Add function keys
    for i in range(1, 13):
        _EVDEV_KEY_MAP[getattr(evdev.ecodes, f'KEY_F{i}')] = f'f{i}'

    def _evdev_code_to_name(code: int) -> Optional[str]:
        """Convert evdev keycode to VoiceFlow key name."""
        if code in _EVDEV_MODIFIER_CODES:
            return _EVDEV_MODIFIER_CODES[code]
        return _EVDEV_KEY_MAP.get(code)

    def _find_keyboard_devices() -> list[evdev.InputDevice]:
        """Find all keyboard input devices."""
        keyboards = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if evdev.ecodes.EV_KEY in caps:
                    key_codes = caps[evdev.ecodes.EV_KEY]
                    # Must have letter keys to be a real keyboard
                    if evdev.ecodes.KEY_A in key_codes and evdev.ecodes.KEY_Z in key_codes:
                        keyboards.append(dev)
            except (PermissionError, OSError):
                continue
        return keyboards


def _pynput_key_to_name(key) -> Optional[str]:
    """Map a pynput key event to a VoiceFlow key name."""
    from pynput.keyboard import Key, KeyCode

    if isinstance(key, KeyCode):
        if key.char:
            return key.char.lower()
        return None

    if not isinstance(key, Key):
        return None

    if key in (Key.ctrl, Key.ctrl_l, Key.ctrl_r):
        return 'ctrl'
    if key in (Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr):
        return 'alt'
    if key in (Key.shift, Key.shift_l, Key.shift_r):
        return 'shift'
    if key in (Key.cmd, Key.cmd_l, Key.cmd_r):
        return 'win'

    named = {
        Key.space: 'space',
        Key.enter: 'enter',
        Key.tab: 'tab',
        Key.esc: 'esc',
        Key.backspace: 'backspace',
        Key.delete: 'delete',
        Key.up: 'up',
        Key.down: 'down',
        Key.left: 'left',
        Key.right: 'right',
    }
    if key in named:
        return named[key]

    for i in range(1, 13):
        fn = getattr(Key, f'f{i}', None)
        if key == fn:
            return f'f{i}'

    return None


class HotkeyService:
    def __init__(self):
        # Callbacks
        self._on_activate: Optional[Callable[[], bool | None]] = None
        self._on_deactivate: Optional[Callable[[], None]] = None

        # Recording state
        self._hold_active = False
        self._toggle_active = False
        self._running = False
        self._max_recording_timer: Optional[threading.Timer] = None

        # Hotkey configuration (defaults)
        self._hold_hotkey: str = "ctrl+win"
        self._hold_hotkey_enabled: bool = True
        self._toggle_hotkey: str = "ctrl+shift+win"
        self._toggle_hotkey_enabled: bool = False

        # Status tracking - exposed to UI so users see why hotkeys are silent
        self._status: dict = {"available": True, "code": "ok", "message": "", "device_count": 0}

        # Shared pressed-key tracking for Linux evdev and macOS pynput
        if IS_LINUX or IS_DARWIN:
            self._pressed_keys: set[str] = set()

        # Linux evdev state
        if IS_LINUX:
            self._evdev_thread: Optional[threading.Thread] = None
            self._evdev_stop = threading.Event()

        # macOS pynput state
        if IS_DARWIN:
            self._pynput_listener = None

    def set_callbacks(
        self,
        on_activate: Callable[[], bool | None],
        on_deactivate: Callable[[], None],
    ):
        """Set callbacks for hotkey activation and deactivation."""
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

    def configure(
        self,
        hold_hotkey: str = None,
        hold_enabled: bool = None,
        toggle_hotkey: str = None,
        toggle_enabled: bool = None,
    ):
        """Update hotkey configuration and re-register handlers if running."""
        needs_restart = False

        # Normalize hotkeys before storing to ensure consistent format
        if hold_hotkey is not None:
            hold_hotkey = normalize_hotkey(hold_hotkey)
            if hold_hotkey != self._hold_hotkey:
                self._hold_hotkey = hold_hotkey
                needs_restart = True
        if hold_enabled is not None and hold_enabled != self._hold_hotkey_enabled:
            self._hold_hotkey_enabled = hold_enabled
            needs_restart = True
        if toggle_hotkey is not None:
            toggle_hotkey = normalize_hotkey(toggle_hotkey)
            if toggle_hotkey != self._toggle_hotkey:
                self._toggle_hotkey = toggle_hotkey
                needs_restart = True
        if toggle_enabled is not None and toggle_enabled != self._toggle_hotkey_enabled:
            self._toggle_hotkey_enabled = toggle_enabled
            needs_restart = True

        if needs_restart and self._running:
            log.info("Hotkey configuration changed, re-registering hotkeys")
            self._unregister_hotkeys()
            self._register_hotkeys()

    def _parse_hotkey_keys(self, hotkey: str) -> list[str]:
        """Parse hotkey string into individual key names for release monitoring."""
        parts = [k.strip().lower() for k in hotkey.split('+')]
        # Normalize windows key variants
        result = []
        for p in parts:
            if p in ('windows', 'left windows', 'right windows'):
                result.append('win')
            else:
                result.append(p)
        return result

    # Hold mode handlers
    def _on_hold_press(self):
        """Called when hold hotkey is pressed."""
        if self._hold_active or self._toggle_active:
            return  # Already recording in some mode

        self._hold_active = True
        log.info("Hold hotkey activated")
        if not self._invoke_activate_callback():
            self._hold_active = False
            self._cancel_max_timer()

    def _deactivate_hold(self):
        """Deactivate hold mode recording."""
        if not self._hold_active:
            return
        self._hold_active = False
        self._cancel_max_timer()
        log.info("Hold hotkey deactivated")
        if self._on_deactivate:
            self._on_deactivate()

    # Toggle mode handlers
    def _on_toggle_press(self):
        """Called when toggle hotkey is pressed - toggles recording state."""
        if self._hold_active:
            return  # Hold mode is active, ignore toggle

        if not self._toggle_active:
            # Start recording
            self._toggle_active = True
            log.info("Toggle hotkey activated - recording started")
            if not self._invoke_activate_callback():
                self._toggle_active = False
                self._cancel_max_timer()
        else:
            # Stop recording
            self._deactivate_toggle()

    def _invoke_activate_callback(self) -> bool:
        """Run the activation callback and report whether recording started."""
        if self._on_activate is None:
            return True
        try:
            result = self._on_activate()
        except Exception as exc:
            log.error("Hotkey activation callback failed", error=str(exc))
            return False
        return result is not False

    def _deactivate_toggle(self):
        """Deactivate toggle mode recording."""
        if not self._toggle_active:
            return
        self._toggle_active = False
        self._cancel_max_timer()
        log.info("Toggle hotkey deactivated - recording stopped")
        if self._on_deactivate:
            self._on_deactivate()

    # Timer management
    def _start_max_timer(self):
        """Start a timer to auto-stop recording after 60 seconds."""
        self._cancel_max_timer()
        self._max_recording_timer = threading.Timer(60.0, self._on_max_timer)
        self._max_recording_timer.daemon = True
        self._max_recording_timer.start()

    def _cancel_max_timer(self):
        """Cancel the max recording timer."""
        if self._max_recording_timer:
            self._max_recording_timer.cancel()
            self._max_recording_timer = None

    def _on_max_timer(self):
        """Called when max recording time is reached."""
        log.info("Max recording time reached (60s)")
        if self._hold_active:
            self._deactivate_hold()
        elif self._toggle_active:
            self._deactivate_toggle()

    # ========================================================================
    # Platform-specific hotkey registration
    # ========================================================================

    def _register_hotkeys(self):
        """Register all enabled hotkeys."""
        if IS_LINUX:
            self._register_hotkeys_evdev()
        elif IS_DARWIN:
            self._register_hotkeys_pynput()
        else:
            self._register_hotkeys_keyboard()

    def _unregister_hotkeys(self):
        """Unregister all hotkeys and release handlers."""
        if IS_LINUX:
            self._unregister_hotkeys_evdev()
        elif IS_DARWIN:
            self._unregister_hotkeys_pynput()
        else:
            self._unregister_hotkeys_keyboard()

    # --- Windows: keyboard library ---

    def _register_hotkeys_keyboard(self):
        """Register hotkeys using the keyboard library (Windows)."""
        import keyboard
        if self._hold_hotkey_enabled and self._hold_hotkey:
            self._register_hold_hotkey_keyboard()
        if self._toggle_hotkey_enabled and self._toggle_hotkey:
            self._register_toggle_hotkey_keyboard()

    def _unregister_hotkeys_keyboard(self):
        """Unregister all hotkeys (Windows)."""
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception as e:
            log.error("Failed to unregister hotkeys", error=str(e))

    def _register_hold_hotkey_keyboard(self):
        """Register hold-to-record hotkey using keyboard library (Windows)."""
        import keyboard
        log.info("Registering hold hotkey (keyboard)", hotkey=self._hold_hotkey)
        try:
            keyboard.add_hotkey(self._hold_hotkey, self._on_hold_press, suppress=False)

            # Monitor key releases to detect when user lets go
            keys = self._parse_hotkey_keys(self._hold_hotkey)
            for key in keys:
                try:
                    keyboard.on_release_key(key, self._check_hold_release_keyboard)
                    if key == 'win':
                        keyboard.on_release_key('windows', self._check_hold_release_keyboard)
                        keyboard.on_release_key('left windows', self._check_hold_release_keyboard)
                        keyboard.on_release_key('right windows', self._check_hold_release_keyboard)
                except Exception as e:
                    log.warning("Failed to register release handler for key", key=key, error=str(e))

            log.info("Hold hotkey registered successfully", hotkey=self._hold_hotkey)
        except Exception as e:
            log.error("Failed to register hold hotkey", hotkey=self._hold_hotkey, error=str(e))

    def _check_hold_release_keyboard(self, event):
        """Check if hold hotkey should be deactivated on key release (Windows)."""
        import keyboard
        if not self._hold_active:
            return

        keys = self._parse_hotkey_keys(self._hold_hotkey)
        all_pressed = True
        for key in keys:
            if key == 'win':
                if not (keyboard.is_pressed('win') or keyboard.is_pressed('windows')):
                    all_pressed = False
                    break
            elif not keyboard.is_pressed(key):
                all_pressed = False
                break

        if not all_pressed:
            log.debug("Hold key released", key=event.name)
            self._deactivate_hold()

    def _register_toggle_hotkey_keyboard(self):
        """Register toggle hotkey using keyboard library (Windows)."""
        import keyboard
        log.info("Registering toggle hotkey (keyboard)", hotkey=self._toggle_hotkey)
        try:
            keyboard.add_hotkey(self._toggle_hotkey, self._on_toggle_press, suppress=False)
            log.info("Toggle hotkey registered successfully", hotkey=self._toggle_hotkey)
        except Exception as e:
            log.error("Failed to register toggle hotkey", hotkey=self._toggle_hotkey, error=str(e))

    # --- macOS: pynput global keyboard listener ---

    def _register_hotkeys_pynput(self):
        """Start pynput listener for hotkey detection on macOS."""
        log.info("Registering hotkeys via pynput")
        self._pressed_keys = set()

        permission_status = self._check_macos_hotkey_permissions()
        if permission_status is not None:
            self._status = permission_status
            return

        try:
            from pynput.keyboard import Listener

            listener = Listener(
                on_press=self._on_pynput_press,
                on_release=self._on_pynput_release,
            )
            self._pynput_listener = listener
            listener.start()
            ready = self._wait_for_pynput_listener(listener)
            listener_alive = getattr(listener, "is_alive", lambda: True)()
            listener_trusted = bool(getattr(listener, "IS_TRUSTED", True))
            if not ready or not listener_alive or not listener_trusted:
                self._unregister_hotkeys_pynput()
                self._status = self._macos_pynput_unavailable_status(
                    trusted=listener_trusted,
                    alive=listener_alive,
                )
                return
        except Exception as e:
            log.error("Failed to start pynput listener", error=str(e))
            self._status = {
                "available": False,
                "code": "pynput_failed",
                "message": (
                    "VoiceFlow couldn't start global hotkey listening. "
                    "Grant Accessibility and Input Monitoring permission in System Settings."
                ),
                "device_count": 0,
            }
            return

        self._status = {
            "available": True,
            "code": "ok",
            "message": "",
            "device_count": 1,
        }
        log.info("pynput listener started")

    def _wait_for_pynput_listener(self, listener, timeout_s: float = 1.0) -> bool:
        condition = getattr(listener, "_condition", None)
        if condition is None:
            return True

        deadline = time.monotonic() + timeout_s
        with condition:
            while not getattr(listener, "_ready", False):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                is_alive = getattr(listener, "is_alive", lambda: True)
                if not is_alive():
                    break
                condition.wait(timeout=remaining)

        return bool(getattr(listener, "_ready", False))

    def _macos_pynput_unavailable_status(self, *, trusted: bool, alive: bool) -> dict:
        if not trusted:
            return {
                "available": False,
                "code": "macos_accessibility_required",
                "message": _macos_permission_message("accessibility"),
                "device_count": 0,
            }

        input_monitoring = self._get_macos_input_monitoring_status()
        if input_monitoring in {"denied", "not_determined"}:
            return {
                "available": False,
                "code": "macos_input_monitoring_required",
                "message": _macos_permission_message("input_monitoring"),
                "device_count": 0,
            }

        log.warning("pynput listener did not become ready", alive=alive)
        return {
            "available": False,
            "code": "pynput_failed",
            "message": (
                "VoiceFlow couldn't start global hotkey listening. "
                "Grant Accessibility and Input Monitoring permission in System Settings."
            ),
            "device_count": 0,
        }

    def _get_macos_input_monitoring_status(self) -> str:
        if not IS_DARWIN:
            return "unknown"
        try:
            from services.macos_permissions import get_input_monitoring_permission_status

            return get_input_monitoring_permission_status()
        except Exception as exc:
            log.warning("macOS input-monitoring check unavailable", error=str(exc))
            return "unknown"

    def _check_macos_hotkey_permissions(self, *, prompt: bool = False) -> Optional[dict]:
        if not IS_DARWIN:
            return None
        try:
            from services.macos_permissions import get_accessibility_permission_status
        except Exception as exc:
            log.warning("macOS permission helpers unavailable", error=str(exc))
            return None

        accessibility = get_accessibility_permission_status(prompt=prompt)
        if accessibility != "granted":
            return {
                "available": False,
                "code": "macos_accessibility_required",
                "message": _macos_permission_message("accessibility"),
                "device_count": 0,
            }

        if prompt:
            try:
                from services.macos_permissions import request_input_monitoring_permission

                request_input_monitoring_permission()
            except Exception as exc:
                log.warning("macOS input-monitoring prompt unavailable", error=str(exc))
        return None

    def _on_pynput_press(self, key):
        name = _pynput_key_to_name(key)
        if not name:
            return
        self._pressed_keys.add(name)
        self._check_hotkey_combo_press()

    def _on_pynput_release(self, key):
        name = _pynput_key_to_name(key)
        if not name:
            return
        if self._hold_active:
            hold_keys = set(self._parse_hotkey_keys(self._hold_hotkey))
            if name in hold_keys:
                log.debug("Hold key released (pynput)", key=name)
                self._deactivate_hold()
        self._pressed_keys.discard(name)

    def _unregister_hotkeys_pynput(self):
        """Stop pynput listener."""
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
                self._pynput_listener.join(timeout=2)
            except Exception as e:
                log.error("Failed to stop pynput listener", error=str(e))
            self._pynput_listener = None
            log.info("pynput listener stopped")

    def _refresh_macos_status(self, *, prompt: bool = False):
        """Refresh macOS permission state and listener registration."""
        if not IS_DARWIN or not self._running:
            return

        permission_status = self._check_macos_hotkey_permissions(prompt=prompt)
        if permission_status is not None:
            if self._pynput_listener is not None:
                self._unregister_hotkeys_pynput()
            self._status = permission_status
            return

        if self._pynput_listener is None:
            log.info("macOS hotkey permissions available, starting listener")
            self._register_hotkeys_pynput()
            return

        listener_alive = getattr(self._pynput_listener, "is_alive", lambda: True)()
        listener_trusted = bool(getattr(self._pynput_listener, "IS_TRUSTED", True))
        if not listener_alive or not listener_trusted:
            self._unregister_hotkeys_pynput()
            self._status = self._macos_pynput_unavailable_status(
                trusted=listener_trusted,
                alive=listener_alive,
            )
            return

        self._status = {
            "available": True,
            "code": "ok",
            "message": "",
            "device_count": 1,
        }

    # --- Linux: evdev (reads directly from /dev/input, bypasses Wayland) ---

    def _register_hotkeys_evdev(self):
        """Start evdev listener thread for hotkey detection on Linux."""
        log.info("Registering hotkeys via evdev")
        self._evdev_stop.clear()
        self._pressed_keys = set()

        keyboards = _find_keyboard_devices()
        if not keyboards:
            log.error("No keyboard devices found - hotkeys will not work. Ensure user is in 'input' group.")
            self._status = {
                "available": False,
                "code": "no_input_access",
                "message": (
                    "VoiceFlow couldn't read any keyboard device, so global "
                    "hotkeys are disabled. Add your user to the 'input' group "
                    "and log out + back in: sudo usermod -aG input $USER"
                ),
                "device_count": 0,
            }
            return

        log.info("Found keyboard devices", count=len(keyboards),
                 devices=[f"{d.name} ({d.path})" for d in keyboards])
        self._status = {
            "available": True,
            "code": "ok",
            "message": "",
            "device_count": len(keyboards),
        }

        self._evdev_thread = threading.Thread(
            target=self._evdev_listener_loop,
            args=(keyboards,),
            daemon=True,
        )
        self._evdev_thread.start()
        log.info("evdev listener started")

    def _evdev_listener_loop(self, keyboards: list):
        """Background thread: read key events from all keyboard devices."""
        devices = {dev.fd: dev for dev in keyboards}

        while not self._evdev_stop.is_set():
            try:
                r, _, _ = select.select(list(devices.keys()), [], [], 0.1)
                for fd in r:
                    dev = devices[fd]
                    try:
                        for event in dev.read():
                            if event.type == evdev.ecodes.EV_KEY:
                                self._handle_evdev_key(event)
                    except OSError:
                        # Device disconnected
                        log.warning("Keyboard device disconnected", device=dev.name)
                        del devices[fd]
                        if not devices:
                            log.error("All keyboard devices disconnected")
                            return
            except Exception as e:
                if not self._evdev_stop.is_set():
                    log.error("evdev listener error", error=str(e))

    def _handle_evdev_key(self, event):
        """Handle a single evdev key event."""
        name = _evdev_code_to_name(event.code)
        if not name:
            return

        if event.value == 1:  # Key press
            self._pressed_keys.add(name)
            self._check_hotkey_combo_press()
        elif event.value == 0:  # Key release
            # Check hold release before removing the key
            if self._hold_active:
                hold_keys = set(self._parse_hotkey_keys(self._hold_hotkey))
                if name in hold_keys:
                    log.debug("Hold key released (evdev)", key=name)
                    self._deactivate_hold()
            self._pressed_keys.discard(name)
        # value == 2 is key repeat, ignore

    def _check_hotkey_combo_press(self):
        """Check if the currently pressed keys match any registered hotkey combo."""
        # Check hold hotkey
        if self._hold_hotkey_enabled and self._hold_hotkey:
            hold_keys = set(self._parse_hotkey_keys(self._hold_hotkey))
            if hold_keys.issubset(self._pressed_keys):
                self._on_hold_press()

        # Check toggle hotkey
        if self._toggle_hotkey_enabled and self._toggle_hotkey:
            toggle_keys = set(self._parse_hotkey_keys(self._toggle_hotkey))
            if toggle_keys.issubset(self._pressed_keys):
                if not self._hold_active:
                    self._on_toggle_press()

    def _unregister_hotkeys_evdev(self):
        """Stop evdev listener thread."""
        if hasattr(self, '_evdev_stop'):
            self._evdev_stop.set()
        if hasattr(self, '_evdev_thread') and self._evdev_thread:
            self._evdev_thread.join(timeout=2)
            self._evdev_thread = None
            log.info("evdev listener stopped")

    # Public API
    def start(self):
        """Start listening for hotkeys."""
        if self._running:
            return

        self._running = True
        self._register_hotkeys()

    def stop(self):
        """Stop listening for hotkeys."""
        self._running = False
        self._unregister_hotkeys()
        self._cancel_max_timer()
        self._hold_active = False
        self._toggle_active = False

    def force_deactivate(self):
        """Manually force deactivation of either mode."""
        log.debug("Force deactivate called")
        if self._hold_active:
            self._deactivate_hold()
        elif self._toggle_active:
            self._deactivate_toggle()

    def manual_start(self) -> bool:
        """Start a recording session triggered from the UI (not a hotkey).

        Reuses toggle-mode bookkeeping so the rest of the controller flow
        (popup state, transcription, paste, history) is identical to a
        hotkey-driven session. Returns True if we started, False if a
        recording was already active.
        """
        if self._hold_active or self._toggle_active:
            log.debug("manual_start ignored - already recording")
            return False
        self._toggle_active = True
        log.info("Manual recording started")
        if not self._invoke_activate_callback():
            self._toggle_active = False
            self._cancel_max_timer()
            return False
        return True

    def manual_stop(self) -> bool:
        """Stop a recording session that was started via manual_start.

        Returns True if we stopped, False if nothing was recording.
        """
        if not (self._hold_active or self._toggle_active):
            return False
        if self._hold_active:
            self._deactivate_hold()
        else:
            self._deactivate_toggle()
        return True

    def is_running(self) -> bool:
        """Return True if the hotkey service is running."""
        return self._running

    def is_recording(self) -> bool:
        """Return True if currently recording in either mode."""
        return self._hold_active or self._toggle_active

    def get_active_mode(self) -> Optional[str]:
        """Return current active mode ('hold', 'toggle') or None."""
        if self._hold_active:
            return "hold"
        elif self._toggle_active:
            return "toggle"
        return None

    def get_status(self, *, prompt: bool = False) -> dict:
        """Return the current hotkey availability status for the UI."""
        self._refresh_macos_status(prompt=prompt)
        return dict(self._status)
