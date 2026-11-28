'use client';

import { useCallback, useSyncExternalStore } from "react";

/**
 * Whether a CSS media query matches, kept up to date as it changes (rotating a phone, plugging a
 * trackpad into a tablet, resizing a window).
 *
 * The server can't evaluate media queries, so it renders `serverDefault`; React corrects it as
 * soon as the page is running in the browser.
 */
export function useMediaQuery(query: string, serverDefault = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => serverDefault,
  );
}
