'use client';

import { useEffect, useRef, useState, type RefObject } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import type { Light, Mesh, MeshStandardMaterial, Object3D } from "three";
import { DoorAutomation } from "./doorAutomation";
import { DoorController } from "./doorController";
import { lobbyConfig, type Interactable, type InteractionAction } from "./lobbyConfig";
import { isPointerLocked, mouseUsesCrosshair } from "./PlayerControls";

const { assets, door: doorConfig, interactables, interaction, render, player } = lobbyConfig;
type InteractableName = keyof typeof interactables;

/** Object name → the interactable it presses: itself, or the button whose hit area it's part of. */
const pressTargets = new Map<string, InteractableName>();
for (const [name, item] of Object.entries(interactables as Record<InteractableName, Interactable>)) {
  pressTargets.set(name, name as InteractableName);
  for (const extra of item.hitArea ?? []) pressTargets.set(extra, name as InteractableName);
}

useGLTF.preload(assets.shell);
useGLTF.preload(assets.floor);

/** GLB scenes are cached and shared, so they're prepared once, not once per mount. */
const prepared = new WeakSet<Object3D>();

function prepare(root: Object3D) {
  if (prepared.has(root)) return;
  prepared.add(root);
  root.traverse((o) => {
    if (!(o as Light).isLight) return;
    (o as Light).intensity *= render.glbLightScale;
    o.visible = render.glbLights; // an invisible light costs nothing: three.js leaves it out of the shaders
  });
  // Give each clickable mesh its own material, so its hover glow doesn't light up everything
  // that shares it (the floor buttons share the lift screen's material).
  for (const name of Object.keys(interactables)) {
    root.getObjectByName(name)?.traverse((o) => {
      const mesh = o as Mesh;
      if (!mesh.isMesh) return;
      const material = (mesh.material as MeshStandardMaterial).clone();
      material.userData.baseEmissiveIntensity = material.emissiveIntensity;
      mesh.material = material;
    });
  }
}

function setGlow(target: Object3D, on: boolean) {
  target.traverse((o) => {
    const material = (o as Mesh).material as MeshStandardMaterial | undefined;
    if (!(o as Mesh).isMesh || !material || material.userData.baseEmissiveIntensity === undefined) return;
    const base = material.userData.baseEmissiveIntensity as number;
    material.emissiveIntensity = on ? base * interaction.hoverGlow : base;
  });
}

/**
 * The interactable pressed by hitting `object`: the nearest clickable ancestor (or the object
 * itself), or the button whose hit area it belongs to, looked up in `scene`.
 */
function findInteractable(object: Object3D | null, scene: Object3D): Object3D | null {
  for (let o = object; o; o = o.parent) {
    const target = pressTargets.get(o.name);
    if (target) return target === o.name ? o : (scene.getObjectByName(target) ?? null);
  }
  return null;
}

/**
 * The lobby (floor_1.glb) and the lift (shell.glb), placed so the room centre is the origin,
 * with the door controller and the clickable buttons.
 */
export function LobbyScene({
  doorRef,
  hover,
  onHoverChange,
}: {
  doorRef: RefObject<DoorController | null>;
  /**
   * Track what the pointer is over (glow + label). Off for touch, which has no hover: without
   * hover handlers, R3F skips hit-testing on every finger move and only tests taps.
   */
  hover: boolean;
  onHoverChange: (label: string | null) => void;
}) {
  const shell = useGLTF(assets.shell);
  const floor = useGLTF(assets.floor);
  // three.js objects are read through get() so they're mutated outside React's render.
  const get = useThree((s) => s.get);
  const hovered = useRef<Object3D | null>(null);
  /** The last pointer was a mouse (aims with the crosshair) rather than touch (taps). */
  const usingMouse = useRef(true);
  /** The current press may click a button (not the click that captures the mouse). */
  const armed = useRef(false);
  const [automation] = useState(() => new DoorAutomation({ ...doorConfig, playerRadius: player.radius }));

  const setHovered = (next: Object3D | null) => {
    const prev = hovered.current;
    if (prev === next) return;
    if (prev) setGlow(prev, false);
    if (next) setGlow(next, true);
    hovered.current = next;
    onHoverChange(next ? interactables[next.name as InteractableName].label : null);
  };

  prepare(shell.scene);
  prepare(floor.scene);

  useEffect(() => {
    const door = new DoorController(shell.scene, shell.animations, doorConfig);
    doorRef.current = door;
    return () => {
      door.dispose();
      doorRef.current = null;
    };
  }, [shell, doorRef]);

  useFrame(({ camera, events, gl }, delta) => {
    // Re-aim every frame while a mouse is aiming (captured, or dragging when capture is refused),
    // so walking with the keys updates what's under the crosshair or cursor, and its reach.
    // Once a captured mouse is released (Esc), nothing is aimed at.
    if (isPointerLocked(gl.domElement) || (usingMouse.current && !mouseUsesCrosshair())) events.update?.();
    else if (usingMouse.current && hovered.current) setHovered(null);

    const door = doorRef.current;
    if (!door) return;
    const dt = Math.min(delta, 0.1);
    automation.update(dt, { x: camera.position.x, z: camera.position.z }, door);
    door.update(dt);
  });

  // Clear the glow if the scene unmounts while something is hovered.
  useEffect(
    () => () => {
      if (hovered.current) setGlow(hovered.current, false);
    },
    [],
  );

  /** The clickable object under the pointer, if it's within reach. */
  const pick = (e: ThreeEvent<PointerEvent | MouseEvent>) => {
    const target = findInteractable(e.object, get().scene);
    return target && e.distance <= interaction.maxReach ? target : null;
  };

  const run = (action: InteractionAction) => {
    const door = doorRef.current;
    if (action.type === "door" && door) door[action.command]();
  };

  // A mouse aims with the crosshair, so it only interacts while captured; touch (and a mouse the
  // browser won't capture) clicks buttons directly.
  const canAim = (e: ThreeEvent<PointerEvent>) =>
    e.pointerType !== "mouse" || !mouseUsesCrosshair() || isPointerLocked(get().gl.domElement);

  // Only the nearest hit counts (stopPropagation), so walls hide buttons behind them.
  const onPointerMove = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    usingMouse.current = e.pointerType === "mouse";
    setHovered(canAim(e) ? pick(e) : null);
  };
  const onPointerOut = () => setHovered(null);
  const onPointerDown = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    usingMouse.current = e.pointerType === "mouse";
    armed.current = canAim(e);
  };
  const onClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (!armed.current) return; // the click that captured the mouse
    if (e.delta > interaction.clickTolerancePx) return; // that was a drag to look around
    const target = pick(e);
    if (target) run(interactables[target.name as InteractableName].action);
  };

  return (
    <group
      position={lobbyConfig.worldOffset}
      onPointerMove={hover ? onPointerMove : undefined}
      onPointerOut={hover ? onPointerOut : undefined}
      onPointerDown={onPointerDown}
      onClick={onClick}
    >
      <primitive object={floor.scene} />
      <primitive object={shell.scene} />
    </group>
  );
}
