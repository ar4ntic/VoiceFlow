import sys
from types import SimpleNamespace

from services import macos_permissions


def test_microphone_permission_maps_avfoundation_states(monkeypatch):
    class _Device:
        status = 0

        @staticmethod
        def authorizationStatusForMediaType_(_media_type):
            return _Device.status

    fake_av = SimpleNamespace(
        AVCaptureDevice=_Device,
        AVMediaTypeAudio="audio",
        AVAuthorizationStatusNotDetermined=0,
        AVAuthorizationStatusRestricted=1,
        AVAuthorizationStatusDenied=2,
        AVAuthorizationStatusAuthorized=3,
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AVFoundation", fake_av)

    _Device.status = 3
    assert macos_permissions.get_microphone_permission_status() == "granted"
    _Device.status = 2
    assert macos_permissions.get_microphone_permission_status() == "denied"
    _Device.status = 1
    assert macos_permissions.get_microphone_permission_status() == "denied"
    _Device.status = 0
    assert macos_permissions.get_microphone_permission_status() == "not_determined"
    _Device.status = 99
    assert macos_permissions.get_microphone_permission_status() == "unknown"


def test_screen_recording_permission_uses_quartz_preflight(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True),
    )

    assert macos_permissions.get_screen_recording_permission_status() == "granted"

    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False),
    )

    assert macos_permissions.get_screen_recording_permission_status() == "not_determined"


def test_accessibility_permission_uses_applicationservices(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def _check(options):
        calls.append(options)
        return True

    fake_app_services = SimpleNamespace(
        AXIsProcessTrustedWithOptions=_check,
        kAXTrustedCheckOptionPrompt="prompt",
    )
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake_app_services)

    assert macos_permissions.get_accessibility_permission_status(prompt=True) == "granted"
    assert calls == [{"prompt": True}]


def test_permission_snapshot_is_unknown_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert macos_permissions.get_macos_permission_snapshot() == {
        "microphone": "unknown",
        "screenRecording": "unknown",
        "accessibility": "unknown",
    }
