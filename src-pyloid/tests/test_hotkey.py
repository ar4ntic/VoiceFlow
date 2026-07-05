import pytest
import sys
import types
from services.hotkey import (
    HotkeyService,
    normalize_hotkey,
    validate_hotkey,
    are_hotkeys_conflicting,
    _macos_permission_message,
    _pynput_key_to_name,
)


class TestNormalizeHotkey:
    def test_simple_modifier_main_key(self):
        """Normalizes simple modifier+key combos."""
        assert normalize_hotkey("ctrl+r") == "ctrl+r"
        assert normalize_hotkey("alt+x") == "alt+x"
        assert normalize_hotkey("shift+a") == "shift+a"

    def test_reorders_modifiers_first(self):
        """Main key comes after modifiers."""
        assert normalize_hotkey("r+ctrl") == "ctrl+r"
        assert normalize_hotkey("x+alt+ctrl") == "ctrl+alt+x"

    def test_canonical_modifier_order(self):
        """Modifiers are ordered: ctrl, alt, shift, win."""
        assert normalize_hotkey("shift+ctrl+alt+win+x") == "ctrl+alt+shift+win+x"
        assert normalize_hotkey("win+shift+alt+ctrl+x") == "ctrl+alt+shift+win+x"

    def test_normalizes_windows_key_variants(self):
        """Normalizes 'windows', 'left windows', 'right windows' to 'win'."""
        assert normalize_hotkey("windows+ctrl") == "ctrl+win"
        assert normalize_hotkey("left windows+alt") == "alt+win"
        assert normalize_hotkey("right windows+shift") == "shift+win"

    def test_normalizes_control_to_ctrl(self):
        """Normalizes 'control' to 'ctrl'."""
        assert normalize_hotkey("control+r") == "ctrl+r"

    def test_removes_duplicate_modifiers(self):
        """Duplicate modifiers are removed."""
        assert normalize_hotkey("ctrl+ctrl+r") == "ctrl+r"

    def test_removes_duplicate_main_keys(self):
        """Duplicate main keys are removed."""
        assert normalize_hotkey("ctrl+r+r") == "ctrl+r"

    def test_multiple_main_keys_sorted_alphabetically(self):
        """Multiple main keys are sorted alphabetically."""
        assert normalize_hotkey("ctrl+b+a") == "ctrl+a+b"

    def test_different_orderings_normalize_to_same(self):
        """Different orderings of same keys normalize to same result."""
        expected = "ctrl+win+r"
        assert normalize_hotkey("ctrl+win+r") == expected
        assert normalize_hotkey("win+ctrl+r") == expected
        assert normalize_hotkey("r+win+ctrl") == expected
        assert normalize_hotkey("r+ctrl+win") == expected

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert normalize_hotkey("") == ""
        assert normalize_hotkey("   ") == "   "

    def test_preserves_case_insensitivity(self):
        """Keys are lowercased."""
        assert normalize_hotkey("CTRL+R") == "ctrl+r"
        assert normalize_hotkey("Ctrl+Win+X") == "ctrl+win+x"


class TestValidateHotkey:
    def test_valid_hotkey_with_modifier_and_key(self):
        """Valid hotkey with modifier and main key."""
        is_valid, error = validate_hotkey("ctrl+r")
        assert is_valid is True
        assert error == ""

    def test_valid_hotkey_multiple_modifiers(self):
        """Valid hotkey with multiple modifiers."""
        is_valid, error = validate_hotkey("ctrl+shift+r")
        assert is_valid is True

    def test_valid_hotkey_two_modifiers_only(self):
        """Two modifiers without main key is valid (e.g., ctrl+win)."""
        is_valid, error = validate_hotkey("ctrl+win")
        assert is_valid is True
        is_valid, error = validate_hotkey("ctrl+shift")
        assert is_valid is True

    def test_invalid_empty_hotkey(self):
        """Empty hotkey is invalid."""
        is_valid, error = validate_hotkey("")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_invalid_single_key(self):
        """Single key without modifier is invalid."""
        is_valid, error = validate_hotkey("r")
        assert is_valid is False

    def test_invalid_single_modifier(self):
        """Single modifier is invalid (needs at least two keys)."""
        is_valid, error = validate_hotkey("ctrl")
        assert is_valid is False
        assert "two keys" in error.lower()


class TestAreHotkeysConflicting:
    def test_identical_hotkeys_conflict(self):
        """Identical hotkeys conflict."""
        assert are_hotkeys_conflicting("ctrl+r", "ctrl+r") is True

    def test_different_order_same_keys_conflict(self):
        """Same keys in different order conflict."""
        assert are_hotkeys_conflicting("ctrl+win+r", "win+ctrl+r") is True
        assert are_hotkeys_conflicting("r+ctrl+win", "ctrl+win+r") is True

    def test_different_hotkeys_no_conflict(self):
        """Different hotkeys don't conflict."""
        assert are_hotkeys_conflicting("ctrl+r", "ctrl+t") is False
        assert are_hotkeys_conflicting("ctrl+win", "ctrl+shift+win") is False

    def test_empty_hotkey_no_conflict(self):
        """Empty hotkey doesn't conflict with anything."""
        assert are_hotkeys_conflicting("", "ctrl+r") is False
        assert are_hotkeys_conflicting("ctrl+r", "") is False

    def test_windows_variants_conflict(self):
        """'win' and 'windows' variants conflict."""
        assert are_hotkeys_conflicting("ctrl+win", "ctrl+windows") is True


class TestPynputKeyMapping:
    @pytest.fixture
    def fake_pynput(self, monkeypatch):
        class Key:
            pass

        for name in (
            "cmd",
            "cmd_l",
            "cmd_r",
            "ctrl",
            "ctrl_l",
            "ctrl_r",
            "alt",
            "alt_l",
            "alt_r",
            "alt_gr",
            "shift",
            "shift_l",
            "shift_r",
            "space",
            "enter",
            "return_key",
            "esc",
            "tab",
            "backspace",
            "delete",
            "up",
            "down",
            "left",
            "right",
        ):
            setattr(Key, name, Key())
        for index in range(1, 13):
            setattr(Key, f"f{index}", Key())
        Key.enter = Key.return_key

        class KeyCode:
            def __init__(self, char=None):
                self.char = char

            @classmethod
            def from_char(cls, char):
                return cls(char)

        keyboard_module = types.ModuleType("pynput.keyboard")
        keyboard_module.Key = Key
        keyboard_module.KeyCode = KeyCode
        pynput_module = types.ModuleType("pynput")
        pynput_module.keyboard = keyboard_module

        monkeypatch.setitem(sys.modules, "pynput", pynput_module)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard_module)
        return Key, KeyCode

    def test_maps_command_to_win(self, fake_pynput):
        Key, _ = fake_pynput
        assert _pynput_key_to_name(Key.cmd) == 'win'

    def test_maps_character_keys(self, fake_pynput):
        _, KeyCode = fake_pynput
        assert _pynput_key_to_name(KeyCode.from_char('R')) == 'r'

    def test_maps_ctrl_modifier(self, fake_pynput):
        Key, _ = fake_pynput
        assert _pynput_key_to_name(Key.ctrl_l) == 'ctrl'


class TestHotkeyService:
    def test_initial_state_not_running(self):
        """Hotkey service starts in non-running state."""
        service = HotkeyService()
        assert service.is_running() == False

    def test_start_changes_state_to_running(self):
        """Starting the service changes state to running."""
        service = HotkeyService()
        service.start()

        assert service.is_running() == True

        # Cleanup
        service.stop()

    def test_stop_changes_state_to_not_running(self):
        """Stopping the service changes state back to not running."""
        service = HotkeyService()
        service.start()
        service.stop()

        assert service.is_running() == False

    def test_start_twice_is_idempotent(self):
        """Starting twice doesn't cause issues."""
        service = HotkeyService()
        service.start()
        service.start()  # Should not error

        assert service.is_running() == True

        # Cleanup
        service.stop()

    def test_stop_without_start_is_safe(self):
        """Stopping without starting doesn't cause issues."""
        service = HotkeyService()
        service.stop()  # Should not error

        assert service.is_running() == False

    def test_set_callbacks(self):
        """Can set activation and deactivation callbacks."""
        service = HotkeyService()

        activated = []
        deactivated = []

        def on_activate():
            activated.append(True)

        def on_deactivate():
            deactivated.append(True)

        # Should not raise
        service.set_callbacks(
            on_activate=on_activate,
            on_deactivate=on_deactivate,
        )

    def test_activation_callback_can_reject_start(self):
        service = HotkeyService()
        service.set_callbacks(
            on_activate=lambda: False,
            on_deactivate=lambda: None,
        )

        service._on_hold_press()

        assert service.is_recording() is False

    def test_callbacks_are_optional(self):
        """Service works without callbacks set."""
        service = HotkeyService()
        service.start()

        # Should not raise when no callbacks are set
        import time
        time.sleep(0.1)

        service.stop()

    def test_get_status_refreshes_macos_permissions_and_starts_listener(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.setattr(hotkey_module, "IS_DARWIN", True)
        monkeypatch.setattr(hotkey_module, "IS_LINUX", False)

        permission_state = {
            "accessibility": "denied",
            "input_monitoring": "denied",
        }

        permissions_module = types.ModuleType("services.macos_permissions")
        permissions_module.get_accessibility_permission_status = (
            lambda prompt=False: permission_state["accessibility"]
        )
        permissions_module.get_input_monitoring_permission_status = (
            lambda: permission_state["input_monitoring"]
        )
        monkeypatch.setitem(sys.modules, "services.macos_permissions", permissions_module)

        starts = []

        class Listener:
            def __init__(self, on_press=None, on_release=None):
                self.on_press = on_press
                self.on_release = on_release

            def start(self):
                starts.append(True)

            def stop(self):
                pass

            def join(self, timeout=None):
                pass

        keyboard_module = types.ModuleType("pynput.keyboard")
        keyboard_module.Listener = Listener
        pynput_module = types.ModuleType("pynput")
        pynput_module.keyboard = keyboard_module
        monkeypatch.setitem(sys.modules, "pynput", pynput_module)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard_module)

        service = HotkeyService()
        service._running = True
        service._status = {
            "available": False,
            "code": "macos_accessibility_required",
            "message": "",
            "device_count": 0,
        }

        assert service.get_status()["available"] is False

        permission_state["accessibility"] = "granted"
        permission_state["input_monitoring"] = "granted"

        status = service.get_status()

        assert status["available"] is True
        assert status["code"] == "ok"
        assert starts == [True]

    def test_get_status_refreshes_macos_permission_revocation(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.setattr(hotkey_module, "IS_DARWIN", True)
        monkeypatch.setattr(hotkey_module, "IS_LINUX", False)

        permissions_module = types.ModuleType("services.macos_permissions")
        permissions_module.get_accessibility_permission_status = lambda prompt=False: "denied"
        permissions_module.get_input_monitoring_permission_status = lambda: "granted"
        monkeypatch.setitem(sys.modules, "services.macos_permissions", permissions_module)

        stops = []
        joins = []

        class Listener:
            def stop(self):
                stops.append(True)

            def join(self, timeout=None):
                joins.append(timeout)

        service = HotkeyService()
        service._running = True
        service._pynput_listener = Listener()
        service._status = {
            "available": True,
            "code": "ok",
            "message": "",
            "device_count": 1,
        }

        status = service.get_status()

        assert status["available"] is False
        assert status["code"] == "macos_accessibility_required"
        assert service._pynput_listener is None
        assert stops == [True]
        assert joins == [2]

    def test_get_status_prompt_requests_macos_input_monitoring_without_blocking_listener(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.setattr(hotkey_module, "IS_DARWIN", True)
        monkeypatch.setattr(hotkey_module, "IS_LINUX", False)

        accessibility_prompts = []
        input_requests = []

        permissions_module = types.ModuleType("services.macos_permissions")
        permissions_module.get_accessibility_permission_status = (
            lambda prompt=False: accessibility_prompts.append(prompt) or "granted"
        )
        permissions_module.get_input_monitoring_permission_status = lambda: "denied"
        permissions_module.request_input_monitoring_permission = (
            lambda: input_requests.append(True) or "denied"
        )
        monkeypatch.setitem(sys.modules, "services.macos_permissions", permissions_module)

        starts = []

        class Listener:
            IS_TRUSTED = True

            def __init__(self, on_press=None, on_release=None):
                self.on_press = on_press
                self.on_release = on_release

            def start(self):
                starts.append(True)

            def stop(self):
                pass

            def join(self, timeout=None):
                pass

        keyboard_module = types.ModuleType("pynput.keyboard")
        keyboard_module.Listener = Listener
        pynput_module = types.ModuleType("pynput")
        pynput_module.keyboard = keyboard_module
        monkeypatch.setitem(sys.modules, "pynput", pynput_module)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard_module)

        service = HotkeyService()
        service._running = True

        status = service.get_status(prompt=True)

        assert status["available"] is True
        assert status["code"] == "ok"
        assert accessibility_prompts == [True, False]
        assert input_requests == [True]
        assert starts == [True]

    def test_dead_macos_listener_reports_input_monitoring_required(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.setattr(hotkey_module, "IS_DARWIN", True)
        monkeypatch.setattr(hotkey_module, "IS_LINUX", False)

        permissions_module = types.ModuleType("services.macos_permissions")
        permissions_module.get_accessibility_permission_status = lambda prompt=False: "granted"
        permissions_module.get_input_monitoring_permission_status = lambda: "denied"
        monkeypatch.setitem(sys.modules, "services.macos_permissions", permissions_module)

        starts = []
        stops = []
        joins = []

        class Listener:
            IS_TRUSTED = True

            def __init__(self, on_press=None, on_release=None):
                self.on_press = on_press
                self.on_release = on_release

            def start(self):
                starts.append(True)

            def is_alive(self):
                return False

            def stop(self):
                stops.append(True)

            def join(self, timeout=None):
                joins.append(timeout)

        keyboard_module = types.ModuleType("pynput.keyboard")
        keyboard_module.Listener = Listener
        pynput_module = types.ModuleType("pynput")
        pynput_module.keyboard = keyboard_module
        monkeypatch.setitem(sys.modules, "pynput", pynput_module)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard_module)

        service = HotkeyService()
        service._running = True

        status = service.get_status()

        assert status["available"] is False
        assert status["code"] == "macos_input_monitoring_required"
        assert service._pynput_listener is None
        assert starts == [True]
        assert stops == [True]
        assert joins == [2]

    def test_macos_permission_message_names_source_run_launcher(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "executable", "/tmp/VoiceFlow/.venv/bin/python3")
        monkeypatch.setattr(hotkey_module, "_macos_source_host_name", lambda: None)

        message = _macos_permission_message("accessibility")

        assert "running from source" in message
        assert "python3" in message
        assert "instead of VoiceFlow.app" in message

    def test_macos_permission_message_names_source_host(self, monkeypatch):
        import services.hotkey as hotkey_module

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(hotkey_module, "_macos_source_host_name", lambda: "Cursor")

        message = _macos_permission_message("accessibility")

        assert "running from Cursor" in message
        assert "Turn on Cursor" in message
        assert "not the VoiceFlow.app entries" in message

    def test_macos_permission_message_mentions_restart_for_packaged_app(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        message = _macos_permission_message("input_monitoring")

        assert "quit and reopen VoiceFlow" in message
