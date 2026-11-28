'use client';

import { useMediaQuery } from "./useMediaQuery";

export type InputDevice = "mouse" | "touch";

/**
 * "mouse" when the main pointer is precise and can hover (a mouse or trackpad, including an iPad
 * with one attached), "touch" when it's a finger (phones, tablets).
 *
 * This checks the input itself rather than the device's brand: iPads report themselves as Macs,
 * and touchscreen laptops have both. The server render assumes "mouse".
 */
export function useInputDevice(): InputDevice {
  return useMediaQuery("(hover: hover) and (pointer: fine)", true) ? "mouse" : "touch";
}

/** Taller than wide. */
export function useIsPortrait(): boolean {
  return useMediaQuery("(orientation: portrait)");
}
