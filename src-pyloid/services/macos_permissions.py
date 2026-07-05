"""macOS privacy-permission helpers."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Literal, TypedDict

from services.logger import get_logger

log = get_logger("settings")

PermissionState = Literal["granted", "denied", "not_determined", "unknown"]
PrivacyPane = Literal["microphone", "screen_recording", "accessibility", "input_monitoring"]


class MacOSPermissionSnapshot(TypedDict):
    microphone: PermissionState
    screenRecording: PermissionState
    accessibility: PermissionState


_PRIVACY_PANE_URLS: dict[PrivacyPane, str] = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


def is_macos() -> bool:
    return sys.platform == "darwin"


def get_macos_permission_snapshot() -> MacOSPermissionSnapshot:
    """Return the permission states exposed to the frontend."""
    if not is_macos():
        return {
            "microphone": "unknown",
            "screenRecording": "unknown",
            "accessibility": "unknown",
        }
    return {
        "microphone": get_microphone_permission_status(),
        "screenRecording": get_screen_recording_permission_status(),
        "accessibility": get_accessibility_permission_status(prompt=False),
    }


def get_microphone_permission_status() -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import AVFoundation  # type: ignore

        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        return _map_av_authorization_status(status, AVFoundation)
    except Exception as exc:
        log.warning("microphone permission check failed", error=str(exc))
        return "unknown"


def request_microphone_permission(timeout_s: float = 30.0) -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import AVFoundation  # type: ignore
    except Exception as exc:
        log.warning("microphone permission request unavailable", error=str(exc))
        return "unknown"

    event = threading.Event()
    granted = {"value": False}

    def _handler(value):
        granted["value"] = bool(value)
        event.set()

    try:
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio,
            _handler,
        )
    except Exception as exc:
        log.warning("microphone permission request failed", error=str(exc))
        return get_microphone_permission_status()

    if not event.wait(timeout=timeout_s):
        return "unknown"
    return "granted" if granted["value"] else "denied"


def get_screen_recording_permission_status() -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import Quartz  # type: ignore

        checker = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if checker is None:
            return "unknown"
        return "granted" if bool(checker()) else "not_determined"
    except Exception as exc:
        log.warning("screen recording permission check failed", error=str(exc))
        return "unknown"


def request_screen_recording_permission() -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import Quartz  # type: ignore

        requester = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if requester is None:
            return get_screen_recording_permission_status()
        return "granted" if bool(requester()) else "denied"
    except Exception as exc:
        log.warning("screen recording permission request failed", error=str(exc))
        return get_screen_recording_permission_status()


def get_accessibility_permission_status(*, prompt: bool = False) -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import ApplicationServices  # type: ignore

        check = ApplicationServices.AXIsProcessTrustedWithOptions
        option_key = ApplicationServices.kAXTrustedCheckOptionPrompt
        trusted = check({option_key: bool(prompt)})
        return "granted" if bool(trusted) else "denied"
    except Exception as exc:
        log.warning("accessibility permission check failed", error=str(exc))
        return "unknown"


def request_accessibility_permission() -> PermissionState:
    return get_accessibility_permission_status(prompt=True)


def get_input_monitoring_permission_status() -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import Quartz  # type: ignore

        checker = getattr(Quartz, "CGPreflightListenEventAccess", None)
        if checker is None:
            return "unknown"
        return "granted" if bool(checker()) else "denied"
    except Exception as exc:
        log.warning("input monitoring permission check failed", error=str(exc))
        return "unknown"


def request_input_monitoring_permission() -> PermissionState:
    if not is_macos():
        return "unknown"
    try:
        import Quartz  # type: ignore

        requester = getattr(Quartz, "CGRequestListenEventAccess", None)
        if requester is None:
            return get_input_monitoring_permission_status()
        return "granted" if bool(requester()) else "denied"
    except Exception as exc:
        log.warning("input monitoring permission request failed", error=str(exc))
        return get_input_monitoring_permission_status()


def open_privacy_settings(pane: PrivacyPane) -> bool:
    if not is_macos():
        return False
    url = _PRIVACY_PANE_URLS.get(pane)
    if not url:
        return False
    try:
        if pane == "accessibility":
            request_accessibility_permission()
        elif pane == "input_monitoring":
            request_input_monitoring_permission()
        subprocess.Popen(["open", url])
        return True
    except Exception as exc:
        log.warning("open privacy settings failed", pane=pane, error=str(exc))
        return False


def _map_av_authorization_status(status, avfoundation_module) -> PermissionState:
    if status == getattr(avfoundation_module, "AVAuthorizationStatusAuthorized", 3):
        return "granted"
    if status == getattr(avfoundation_module, "AVAuthorizationStatusDenied", 2):
        return "denied"
    if status == getattr(avfoundation_module, "AVAuthorizationStatusRestricted", 1):
        return "denied"
    if status == getattr(avfoundation_module, "AVAuthorizationStatusNotDetermined", 0):
        return "not_determined"
    return "unknown"
