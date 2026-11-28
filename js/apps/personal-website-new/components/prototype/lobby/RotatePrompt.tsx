'use client';

import { MdScreenRotation } from "react-icons/md";

/** `lock` isn't in every browser (or TypeScript's DOM types): Android Chrome has it, iOS doesn't. */
type LockableOrientation = ScreenOrientation & { lock?: (orientation: "landscape") => Promise<void> };

/** Android Chrome can go fullscreen and lock to landscape after a tap; iPhones can't do either. */
function canLockLandscape() {
  const orientation = screen.orientation as LockableOrientation | undefined;
  return typeof document.documentElement.requestFullscreen === "function" && typeof orientation?.lock === "function";
}

async function enterLandscape() {
  try {
    await document.documentElement.requestFullscreen({ navigationUI: "hide" });
    await (screen.orientation as LockableOrientation).lock?.("landscape");
  } catch {
    // Not allowed here; the prompt stays up until the phone is turned.
  }
}

/**
 * Covers the lobby while a phone is held upright. The web can't force landscape everywhere, so
 * this asks for it, and on browsers that can lock the screen (Android) offers a one-tap button.
 * Only rendered in the browser (touch and portrait are unknown on the server).
 */
export function RotatePrompt() {
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-background/95 px-8 text-center">
      <MdScreenRotation aria-hidden className="mb-2 h-12 w-12 animate-pulse text-white/80" />
      <p className="text-lg font-medium">Turn your phone sideways</p>
      <p className="max-w-xs text-sm text-white/60">The lobby is made for landscape.</p>
      {canLockLandscape() && (
        <button
          type="button"
          onClick={enterLandscape}
          className="mt-3 rounded-full bg-primary px-5 py-2 text-sm font-medium text-white"
        >
          Go fullscreen in landscape
        </button>
      )}
    </div>
  );
}
