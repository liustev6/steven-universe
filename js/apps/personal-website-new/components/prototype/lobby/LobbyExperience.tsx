'use client';

import { Suspense, useRef, useState, useSyncExternalStore } from "react";
import { Canvas, events as pointerEvents } from "@react-three/fiber";
import { PerformanceMonitor, useProgress } from "@react-three/drei";
import { AgXToneMapping } from "three";
import { useInputDevice, useIsPortrait } from "@/hooks/useInputDevice";
import { ColliderDebug } from "./ColliderDebug";
import type { DoorController } from "./doorController";
import { NO_MOVE, type MoveInput } from "./joystick";
import { LobbyLighting } from "./LobbyLighting";
import { LobbyScene } from "./LobbyScene";
import { lobbyConfig } from "./lobbyConfig";
import { PlayerControls } from "./PlayerControls";
import { RotatePrompt } from "./RotatePrompt";
import { TouchJoystick } from "./TouchJoystick";

/**
 * R3F's pointer events, except that while the mouse is captured (pointer lock) every raycast
 * goes through the screen centre, where the crosshair is: a locked mouse has no position.
 */
const crosshairEvents: typeof pointerEvents = (store) => ({
  ...pointerEvents(store),
  compute(event, state) {
    if (document.pointerLockElement === state.gl.domElement) state.pointer.set(0, 0);
    else state.pointer.set((event.offsetX / state.size.width) * 2 - 1, -(event.offsetY / state.size.height) * 2 + 1);
    state.raycaster.setFromCamera(state.pointer, state.camera);
  },
});

const subscribePointerLock = (onChange: () => void) => {
  document.addEventListener("pointerlockchange", onChange);
  return () => document.removeEventListener("pointerlockchange", onChange);
};

function LoadingOverlay() {
  const { active, progress } = useProgress();
  if (!active) return null;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background">
      <p className="text-sm uppercase tracking-[0.3em] text-white/60">Loading lobby {Math.round(progress)}%</p>
    </div>
  );
}

/**
 * The walkable lobby: a full-screen canvas. With a mouse: a hint, and a crosshair while the mouse
 * is captured. With touch: a walking stick in landscape, and a prompt to turn the phone in portrait.
 */
export default function LobbyExperience() {
  const { camera, player, render } = lobbyConfig;
  const doorRef = useRef<DoorController | null>(null);
  const moveRef = useRef<MoveInput>(NO_MOVE);
  const device = useInputDevice();
  const portrait = useIsPortrait();
  const [hoverLabel, setHoverLabel] = useState<string | null>(null);
  /** Lowered while the frame rate is struggling; raised again once it recovers. */
  const [resolutionLowered, setResolutionLowered] = useState(false);
  const maxResolution = resolutionLowered ? render.resolution.lowered : render.resolution[device];
  const mouseCaptured = useSyncExternalStore(
    subscribePointerLock,
    () => document.pointerLockElement !== null,
    () => false,
  );

  return (
    <div className="relative h-dvh w-full select-none overflow-hidden">
      <Canvas
        camera={{
          fov: camera.fov,
          near: camera.near,
          far: camera.far,
          position: [player.spawn.x, player.eyeHeight, player.spawn.z],
        }}
        dpr={[1, maxResolution]}
        events={crosshairEvents}
        gl={{ antialias: true, powerPreference: "high-performance" }}
        onCreated={({ gl }) => {
          gl.toneMapping = AgXToneMapping; // the view transform the scene was lit with in Blender
          gl.toneMappingExposure = render.exposure;
        }}
      >
        <color attach="background" args={[render.background]} />
        <PerformanceMonitor
          onDecline={() => setResolutionLowered(true)}
          onIncline={() => setResolutionLowered(false)}
          onFallback={() => setResolutionLowered(true)}
          flipflops={3} // after 3 back-and-forths, stay lowered rather than keep switching
        />
        <LobbyLighting />
        <Suspense fallback={null}>
          <LobbyScene doorRef={doorRef} hover={device === "mouse"} onHoverChange={setHoverLabel} />
        </Suspense>
        <PlayerControls doorRef={doorRef} moveRef={moveRef} />
        <ColliderDebug />
      </Canvas>

      <LoadingOverlay />

      {device === "mouse" && (
        // How to switch mouse mode from the current one.
        <p className="pointer-events-none absolute right-4 top-4 flex items-center rounded-lg bg-black/45 px-3 py-2 text-xs text-white/85 backdrop-blur">
          <kbd className="mr-1.5 rounded border border-white/30 px-1.5 py-0.5 font-sans">
            {mouseCaptured ? "Esc" : "Click"}
          </kbd>
          {mouseCaptured ? "to release the mouse" : "to look around"}
        </p>
      )}
      {device === "touch" && !portrait && <TouchJoystick moveRef={moveRef} />}
      {device === "touch" && portrait && <RotatePrompt />}

      {/* The crosshair is for aiming a captured mouse; on touch you tap the button itself. */}
      {device === "mouse" && mouseCaptured && (
        <div className="pointer-events-none absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/90 ring-1 ring-black/40" />
      )}
      {hoverLabel && (
        <div className="pointer-events-none absolute left-1/2 top-[calc(50%+18px)] -translate-x-1/2 rounded-full bg-black/60 px-3 py-1 text-xs text-white/90 backdrop-blur">
          {hoverLabel}
        </div>
      )}
    </div>
  );
}
