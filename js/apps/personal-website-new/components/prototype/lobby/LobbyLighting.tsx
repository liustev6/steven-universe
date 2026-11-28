'use client';

import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import { Color, PMREMGenerator, type RectAreaLight } from "three";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { lobbyConfig, type AreaLightConfig } from "./lobbyConfig";

RectAreaLightUniformsLib.init();

/** Blender area-light watts → three.js luminance (nits-like), for a one-sided emitter. */
const luminance = ({ blenderWatts, width, height }: AreaLightConfig) => blenderWatts / (Math.PI * width * height);

/**
 * The light the GLBs can't carry: Blender's two area lights, a fill standing in for bounce light,
 * and a studio environment for reflections. The GLB's own spot lights come in with the floor.
 */
export function LobbyLighting() {
  const { render } = lobbyConfig;
  // three.js objects are read through get() so they're mutated outside React's render.
  const get = useThree((s) => s.get);

  useEffect(() => {
    const { gl, scene } = get();
    const pmrem = new PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const env = pmrem.fromScene(room, 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = render.environmentIntensity;
    return () => {
      scene.environment = null;
      env.dispose();
      room.dispose();
      pmrem.dispose();
    };
  }, [get, render.environmentIntensity]);

  return (
    <>
      {/* One hemisphere light per direction, so the up/down balance is set by intensity alone. */}
      <hemisphereLight args={[render.bounceFill.color, "#000000", render.bounceFill.fromAbove]} />
      <hemisphereLight args={["#000000", render.bounceFill.color, render.bounceFill.fromBelow]} />
      {render.areaLights.map((light) => (
        <rectAreaLight
          key={light.name}
          name={light.name}
          args={[new Color().setRGB(...light.color), luminance(light), light.width, light.height]}
          position={light.position}
          ref={(l: RectAreaLight | null) => l?.lookAt(...light.target)}
        />
      ))}
    </>
  );
}
