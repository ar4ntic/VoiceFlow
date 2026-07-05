import { Keyboard, Sparkles } from "lucide-react";
import { formatHotkeyKeyForDisplay } from "@/lib/utils";

// ============================================================================
// STEP: FINAL
// ============================================================================

export const StepFinal = () => (
  <div className="space-y-5 max-w-lg w-full">
    <div className="border border-border rounded-md bg-surface p-8 space-y-5">
      <div className="text-center space-y-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-cream-muted/60 flex items-center justify-center gap-2">
          <Keyboard className="w-3 h-3 text-accent-500" strokeWidth={2.5} />
          global shortcut
        </p>
        <div className="flex items-center justify-center gap-3 pt-1">
          <kbd className="min-w-[72px] py-2.5 rounded-md bg-secondary border border-border text-base font-mono font-medium text-cream">
            {formatHotkeyKeyForDisplay("ctrl")}
          </kbd>
          <span className="text-base text-cream-muted/40 font-mono">+</span>
          <kbd className="min-w-[72px] py-2.5 rounded-md bg-secondary border border-border text-base font-mono font-medium text-cream">
            {formatHotkeyKeyForDisplay("win")}
          </kbd>
        </div>
        <p className="text-sm text-cream-muted">
          Hold to record, release to transcribe.
        </p>
      </div>
    </div>

    <div className="flex items-center gap-3 px-4 py-3 border-l-2 border-accent-500/40">
      <Sparkles
        className="w-4 h-4 text-accent-500 flex-shrink-0"
        strokeWidth={2}
      />
      <p className="text-sm text-cream-muted leading-relaxed">
        VoiceFlow runs quietly in your system tray. Press the shortcut anytime,
        anywhere to start dictating.
      </p>
    </div>
  </div>
);
