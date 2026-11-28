'use client';

import { useEffect, useRef, type PointerEvent, type RefObject } from "react";
import { NO_MOVE, stickToMove, type MoveInput } from "./joystick";
import { lobbyConfig } from "./lobbyConfig";

/**
 * An on-screen thumbstick for walking on touch screens, bottom-left. It writes into `moveRef`,
 * which PlayerControls reads every frame. Looking around stays a one-finger drag anywhere else,
 * so one thumb walks while the other looks.
 */
export function TouchJoystick({ moveRef }: { moveRef: RefObject<MoveInput> }) {
  const { joystickSize, deadzone } = lobbyConfig.touch;
  const knobRef = useRef<HTMLDivElement>(null);
  const pointerRef = useRef<number | null>(null);

  const follow = (e: PointerEvent<HTMLDivElement>) => {
    // The stick's size comes from CSS (it scales with the screen), so measure it as drawn.
    const rect = e.currentTarget.getBoundingClientRect();
    const { move, knob } = stickToMove(
      e.clientX - (rect.left + rect.width / 2),
      e.clientY - (rect.top + rect.height / 2),
      rect.width / 2,
      deadzone,
    );
    moveRef.current = move;
    if (knobRef.current) knobRef.current.style.transform = `translate(${knob.x}px, ${knob.y}px)`;
  };

  const release = () => {
    pointerRef.current = null;
    moveRef.current = NO_MOVE;
    if (knobRef.current) knobRef.current.style.transform = "";
  };

  // Stop walking if the stick disappears mid-push (e.g. the phone is turned to portrait).
  useEffect(
    () => () => {
      moveRef.current = NO_MOVE;
    },
    [moveRef],
  );

  return (
    <div
      aria-label="Walk"
      className="absolute touch-none select-none rounded-full border border-white/15 bg-white/5"
      style={{
        width: joystickSize,
        height: joystickSize,
        left: "calc(env(safe-area-inset-left) + 28px)",
        bottom: "calc(env(safe-area-inset-bottom) + 28px)",
      }}
      onPointerDown={(e) => {
        if (pointerRef.current !== null) return;
        pointerRef.current = e.pointerId;
        e.currentTarget.setPointerCapture(e.pointerId);
        follow(e);
      }}
      onPointerMove={(e) => {
        if (e.pointerId === pointerRef.current) follow(e);
      }}
      onPointerUp={(e) => {
        if (e.pointerId === pointerRef.current) release();
      }}
      onPointerCancel={(e) => {
        if (e.pointerId === pointerRef.current) release();
      }}
    >
      <div
        ref={knobRef}
        className="pointer-events-none absolute inset-[29%] rounded-full bg-white/35"
      />
    </div>
  );
}
