from services import clipboard as clipboard_module
from services.clipboard import ClipboardService


class _FakePyAutoGUI:
    def __init__(self):
        self.calls = []

    def hotkey(self, *keys):
        self.calls.append(keys)


def test_macos_paste_uses_command_v(monkeypatch):
    monkeypatch.setattr(clipboard_module, "IS_WAYLAND", False)
    monkeypatch.setattr(clipboard_module, "IS_DARWIN", True)
    service = ClipboardService()
    fake = _FakePyAutoGUI()
    service._pyautogui = fake

    service._simulate_paste_keystroke()

    assert fake.calls == [("command", "v")]


def test_non_macos_paste_uses_ctrl_v(monkeypatch):
    monkeypatch.setattr(clipboard_module, "IS_WAYLAND", False)
    monkeypatch.setattr(clipboard_module, "IS_DARWIN", False)
    service = ClipboardService()
    fake = _FakePyAutoGUI()
    service._pyautogui = fake

    service._simulate_paste_keystroke()

    assert fake.calls == [("ctrl", "v")]
