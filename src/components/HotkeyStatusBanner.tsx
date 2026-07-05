import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, RefreshCw, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const DISMISS_KEY = "voiceflow:hotkey-banner-dismissed-code";

type HotkeyStatus = {
  available: boolean;
  code: string;
  message: string;
};

export function HotkeyStatusBanner() {
  const mountedRef = useRef(true);
  const [status, setStatus] = useState<HotkeyStatus | null>(null);
  const [isManualChecking, setIsManualChecking] = useState(false);
  const [dismissedCode, setDismissedCode] = useState<string | null>(() => {
    try {
      return localStorage.getItem(DISMISS_KEY);
    } catch {
      return null;
    }
  });

  const checkStatus = useCallback(async (options?: { prompt?: boolean }) => {
    try {
      const nextStatus = await api.getHotkeyStatus(options);
      if (!mountedRef.current) return;
      setStatus(nextStatus);

      if (nextStatus.available) {
        try {
          localStorage.removeItem(DISMISS_KEY);
        } catch {
          // ignore
        }
        setDismissedCode(null);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    checkStatus();

    const checkQuietly = () => {
      void checkStatus();
    };
    const checkWhenVisible = () => {
      if (!document.hidden) void checkStatus();
    };

    const id = window.setInterval(checkQuietly, 10000);
    window.addEventListener("focus", checkQuietly);
    document.addEventListener("visibilitychange", checkWhenVisible);

    return () => {
      mountedRef.current = false;
      window.clearInterval(id);
      window.removeEventListener("focus", checkQuietly);
      document.removeEventListener("visibilitychange", checkWhenVisible);
    };
  }, [checkStatus]);

  if (!status || status.available) return null;
  if (status.code === dismissedCode) return null;

  const handleDismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, status.code);
    } catch {
      // ignore
    }
    setDismissedCode(status.code);
  };

  const macOSPane =
    status.code === "macos_input_monitoring_required"
      ? "input_monitoring"
      : status.code === "macos_accessibility_required" || status.code === "pynput_failed"
        ? "accessibility"
        : null;

  const handleOpenSettings = async () => {
    if (!macOSPane) return;
    await api.openMacOSPrivacySettings(macOSPane);
    window.setTimeout(checkStatus, 500);
    window.setTimeout(checkStatus, 2500);
  };

  const handleCheckAgain = async () => {
    setIsManualChecking(true);
    try {
      await checkStatus({ prompt: true });
    } finally {
      if (mountedRef.current) setIsManualChecking(false);
    }
  };

  return (
    <div className="bg-amber-500/10 border-b border-amber-500/30 px-4 md:px-8 py-3 flex items-start gap-3 text-sm">
      <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="font-medium text-amber-700 dark:text-amber-300">
          Global hotkeys are disabled
        </p>
        <p className="text-amber-700/80 dark:text-amber-300/80 mt-0.5">
          {status.message} You can still use the Record button on the dashboard.
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {macOSPane && (
            <button
              type="button"
              onClick={handleOpenSettings}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-800 dark:text-amber-200 hover:text-amber-900 dark:hover:text-amber-100"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Open System Settings
            </button>
          )}
          <button
            type="button"
            onClick={handleCheckAgain}
            disabled={isManualChecking}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-800/80 dark:text-amber-200/80 hover:text-amber-900 dark:hover:text-amber-100 disabled:opacity-60"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", isManualChecking && "animate-spin")}
            />
            {isManualChecking ? "Checking" : "Check again"}
          </button>
        </div>
      </div>
      <button
        type="button"
        onClick={handleDismiss}
        className="text-amber-700/60 dark:text-amber-300/60 hover:text-amber-700 dark:hover:text-amber-300 p-1 -m-1"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
