import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

type ShortcutPlatform = "mac" | "linux" | "windows"

const HOTKEY_DISPLAY_MAP: Record<ShortcutPlatform, Record<string, string>> = {
  mac: {
    ctrl: "Control",
    alt: "Option",
    shift: "Shift",
    win: "Command",
  },
  linux: {
    ctrl: "Ctrl",
    alt: "Alt",
    shift: "Shift",
    win: "Super",
  },
  windows: {
    ctrl: "Ctrl",
    alt: "Alt",
    shift: "Shift",
    win: "Win",
  },
}

function getShortcutPlatform(): ShortcutPlatform {
  if (typeof navigator === "undefined") return "windows"

  const nav = navigator as Navigator & {
    userAgentData?: { platform?: string }
  }
  const platform = [
    nav.userAgentData?.platform,
    nav.platform,
    nav.userAgent,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()

  if (platform.includes("mac")) return "mac"
  if (platform.includes("linux")) return "linux"
  return "windows"
}

export function isMacPlatform(): boolean {
  return getShortcutPlatform() === "mac"
}

export function isLinuxPlatform(): boolean {
  return getShortcutPlatform() === "linux"
}

export function formatHotkeyKeyForDisplay(key: string): string {
  const normalized = key.trim().toLowerCase()
  const platform = getShortcutPlatform()
  if (HOTKEY_DISPLAY_MAP[platform][normalized]) {
    return HOTKEY_DISPLAY_MAP[platform][normalized]
  }

  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

export function formatHotkeyForDisplay(hotkey: string | null | undefined): string {
  if (!hotkey) return ""
  return hotkey
    .split("+")
    .map((part) => formatHotkeyKeyForDisplay(part))
    .join("+")
}
