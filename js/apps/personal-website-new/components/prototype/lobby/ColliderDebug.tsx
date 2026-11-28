'use client';

import { useMemo } from "react";
import { BufferGeometry, Float32BufferAttribute } from "three";
import { arc, v2, type Collider, type Vec2 } from "./collision";
import { lobbyConfig, staticColliders } from "./lobbyConfig";

/** A collider's outline as line pairs on the floor plane. */
function outline(c: Collider): Vec2[] {
  const loop = (pts: Vec2[]) => pts.flatMap((p, i) => (i ? [pts[i - 1], p] : []));
  switch (c.kind) {
    case "segment":
      return [c.a, c.b];
    case "circle":
      return loop(arc(c.center, c.radius, 0, 360, 15));
    case "roundedBox": {
      const ix = c.half.x - c.cornerRadius;
      const iz = c.half.z - c.cornerRadius;
      const corner = (sx: number, sz: number, from: number) =>
        arc(v2(c.center.x + sx * ix, c.center.z + sz * iz), c.cornerRadius, from, from + 90, 15);
      const pts = [...corner(1, 1, 0), ...corner(-1, 1, 90), ...corner(-1, -1, 180), ...corner(1, -1, 270)];
      return loop([...pts, pts[0]]);
    }
  }
}

/**
 * Draws the static colliders just above the floor, when `debug.showColliders` is on or the URL
 * has ?colliders. Canvas children only render in the browser, so reading the URL here is safe.
 */
export function ColliderDebug() {
  const enabled = useMemo(
    () => lobbyConfig.debug.showColliders || new URLSearchParams(window.location.search).has("colliders"),
    [],
  );
  const geometry = useMemo(() => {
    const pts = staticColliders.flatMap(outline);
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(pts.flatMap((p) => [p.x, 0.02, p.z]), 3));
    return g;
  }, []);

  if (!enabled) return null;
  return (
    <lineSegments geometry={geometry} renderOrder={10}>
      <lineBasicMaterial color="#ff2d55" depthTest={false} transparent />
    </lineSegments>
  );
}
