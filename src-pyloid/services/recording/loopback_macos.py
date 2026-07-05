"""macOS ScreenCaptureKit loopback discovery."""

from __future__ import annotations

import sys
from typing import Any

MACOS_SCREENCAPTUREKIT_ID = -10_001


def list_macos_loopback_sources() -> list[dict[str, Any]]:
    if sys.platform != "darwin":
        return []
    return [
        {
            "id": MACOS_SCREENCAPTUREKIT_ID,
            "name": "System audio",
            "kind": "loopback",
            "hostApi": "ScreenCaptureKit",
            "isDefault": True,
        }
    ]
