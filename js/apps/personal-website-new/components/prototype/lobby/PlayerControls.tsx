'use client';

import { useEffect, useRef, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { moveCircle, v2 } from "./collision";
import type { DoorController } from "./doorController";
import type { MoveInput } from "./joystick";
import { doorPanelColliders, lobbyConfig, staticColliders } from "./lobbyConfig";

const MOVE_KEYS: Record<string, [forward: number, strafe: number]> = {
  KeyW: [1, 0],
  ArrowUp: [1, 0],
  KeyS: [-1, 0],
  ArrowDown: [-1, 0],
  KeyD: [0, 1],
  ArrowRight: [0, 1],
  KeyA: [0, -1],
  ArrowLeft: [0, -1],
};

/** The mouse is captured for looking around (after the first click on the canvas). */
export const isPointerLocked = (canvas: HTMLElement) => document.pointerLockElement === canvas;

/**
 * Some browsers and embeds refuse to capture the mouse. If capture has never worked on this
 * visit and is refused, the mouse falls back to dragging to look and clicking buttons directly,
 * like touch. (Once it has worked, a refusal is only Chrome's cool-down right after Esc.)
 */
let pointerLockWorked = false;
let pointerLockRefused = false;
export const mouseUsesCrosshair = () => !pointerLockRefused;

/**
 * First-person walking, FPS-style: click to capture the mouse and look around with it, WASD or
 * the arrow keys to move. On touch screens, drag to look and walk with the on-screen stick
 * (`moveRef`). The player is a circle on the floor plane that slides along the lobby's
 * colliders, including the door panels.
 */
export function PlayerControls({
  doorRef,
  moveRef,
}: {
  doorRef: RefObject<DoorController | null>;
  moveRef: RefObject<MoveInput>;
}) {
  // three.js objects are read through get() so they're mutated outside React's render.
  const get = useThree((s) => s.get);
  const { player } = lobbyConfig;

  const position = useRef(v2(player.spawn.x, player.spawn.z));
  const yaw = useRef(player.spawnYaw);
  const pitch = useRef(0);
  const pressed = useRef(new Set<string>());

  // Mouse: FPS-style. Click once to capture the mouse, then moving it turns the view; Esc lets
  // go (the browser handles that). Touch and pen can't capture the pointer, so they drag to look.
  useEffect(() => {
    const canvas = get().gl.domElement;
    canvas.style.touchAction = "none";
    let dragging: { id: number; x: number; y: number } | null = null;

    const turn = (dYaw: number, dPitch: number) => {
      yaw.current += dYaw;
      pitch.current = Math.max(-player.maxPitch, Math.min(player.maxPitch, pitch.current + dPitch));
    };

    const dragsToLook = (e: PointerEvent) => e.pointerType !== "mouse" || pointerLockRefused;

    const down = (e: PointerEvent) => {
      if (!dragsToLook(e)) {
        // Errors are reported through "pointerlockerror" (handled below).
        if (e.button === 0 && !isPointerLocked(canvas)) canvas.requestPointerLock().catch(() => {});
        return;
      }
      if (dragging) return;
      dragging = { id: e.pointerId, x: e.clientX, y: e.clientY };
      canvas.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!dragsToLook(e)) {
        // Mouse right turns right, mouse up looks up.
        if (isPointerLocked(canvas)) turn(-e.movementX * player.mouseSensitivity, -e.movementY * player.mouseSensitivity);
        return;
      }
      if (!dragging || e.pointerId !== dragging.id) return;
      // Like a mobile shooter (and the mouse): swipe right to turn right, swipe up to look up.
      turn(-(e.clientX - dragging.x) * player.touchSensitivity, -(e.clientY - dragging.y) * player.touchSensitivity);
      dragging.x = e.clientX;
      dragging.y = e.clientY;
    };
    const up = (e: PointerEvent) => {
      if (dragging?.id === e.pointerId) dragging = null;
    };

    const changed = () => {
      if (isPointerLocked(canvas)) pointerLockWorked = true;
    };
    const refused = () => {
      if (!pointerLockWorked) pointerLockRefused = true;
    };

    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    document.addEventListener("pointerlockchange", changed);
    document.addEventListener("pointerlockerror", refused);
    return () => {
      document.removeEventListener("pointerlockchange", changed);
      document.removeEventListener("pointerlockerror", refused);
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      if (isPointerLocked(canvas)) document.exitPointerLock();
    };
  }, [get, player.mouseSensitivity, player.touchSensitivity, player.maxPitch]);

  // Movement keys.
  useEffect(() => {
    const keys = pressed.current;
    const typing = (e: KeyboardEvent) =>
      e.target instanceof HTMLElement && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName));
    const down = (e: KeyboardEvent) => {
      if (!(e.code in MOVE_KEYS) || typing(e) || e.metaKey || e.ctrlKey) return;
      keys.add(e.code);
      e.preventDefault(); // arrow keys would scroll the page
    };
    const up = (e: KeyboardEvent) => keys.delete(e.code);
    const clear = () => keys.clear();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", clear);
    };
  }, []);

  useFrame(({ camera }, delta) => {
    const dt = Math.min(delta, 0.05); // a long frame (tab switch) mustn't launch the player

    // Keys and the touch stick add up; the stick is analog, so a light push walks slowly.
    let forward = moveRef.current.forward;
    let strafe = moveRef.current.strafe;
    for (const code of pressed.current) {
      forward += MOVE_KEYS[code][0];
      strafe += MOVE_KEYS[code][1];
    }
    const length = Math.hypot(forward, strafe);
    const step = (player.walkSpeed * dt) / Math.max(1, length); // never faster than walking speed
    const sin = Math.sin(yaw.current);
    const cos = Math.cos(yaw.current);
    // Facing direction at yaw 0 is -Z; right is +X.
    const dx = (-sin * forward + cos * strafe) * step;
    const dz = (-cos * forward - sin * strafe) * step;

    const door = doorRef.current;
    const colliders = door ? [...staticColliders, ...doorPanelColliders(door.slid)] : staticColliders;
    // Resolve even when standing still: a closing door panel must not end up inside the player.
    moveCircle(position.current, dx, dz, player.radius, colliders);

    camera.position.set(position.current.x, player.eyeHeight, position.current.z);
    camera.rotation.set(pitch.current, yaw.current, 0, "YXZ");
  });

  return null;
}
