/**
 * 2D collision on the floor plane (three.js X/Z). The player is a circle; the world is a list of
 * colliders the circle is pushed out of. The lobby is flat, so this is all the physics it needs,
 * and it stays exact where a mesh-based collider would snag on bevels and baseboards.
 */

export type Vec2 = { x: number; z: number };

/** A wall with no thickness, blocking from both sides. */
export type Segment = { kind: "segment"; a: Vec2; b: Vec2 };
/** A round obstacle, e.g. a pillar's plinth. */
export type Circle = { kind: "circle"; center: Vec2; radius: number };
/** An axis-aligned box with rounded corners, e.g. the reception desk. */
export type RoundedBox = { kind: "roundedBox"; center: Vec2; half: Vec2; cornerRadius: number };

export type Collider = Segment | Circle | RoundedBox;

export const v2 = (x: number, z: number): Vec2 => ({ x, z });

/** Points along a circular arc, angles in degrees measured from +X toward +Z. */
export function arc(center: Vec2, radius: number, fromDeg: number, toDeg: number, stepDeg = 5): Vec2[] {
  const steps = Math.max(1, Math.ceil(Math.abs(toDeg - fromDeg) / stepDeg));
  return Array.from({ length: steps + 1 }, (_, i) => {
    const a = ((fromDeg + ((toDeg - fromDeg) * i) / steps) * Math.PI) / 180;
    return v2(center.x + radius * Math.cos(a), center.z + radius * Math.sin(a));
  });
}

/** Consecutive points joined into wall segments. */
export function polyline(points: Vec2[], closed = false): Segment[] {
  const segments: Segment[] = [];
  for (let i = 0; i < points.length - 1; i++) segments.push({ kind: "segment", a: points[i], b: points[i + 1] });
  if (closed && points.length > 2) segments.push({ kind: "segment", a: points[points.length - 1], b: points[0] });
  return segments;
}

/** Push p (in place) out of a circle of radius r centred at c. */
function pushFromPoint(p: Vec2, cx: number, cz: number, r: number) {
  const dx = p.x - cx;
  const dz = p.z - cz;
  const d2 = dx * dx + dz * dz;
  if (d2 >= r * r || d2 < 1e-12) return;
  const d = Math.sqrt(d2);
  const k = (r - d) / d;
  p.x += dx * k;
  p.z += dz * k;
}

function pushOut(p: Vec2, r: number, c: Collider) {
  switch (c.kind) {
    case "segment": {
      const abx = c.b.x - c.a.x;
      const abz = c.b.z - c.a.z;
      const len2 = abx * abx + abz * abz;
      const t = len2 > 0 ? Math.min(1, Math.max(0, ((p.x - c.a.x) * abx + (p.z - c.a.z) * abz) / len2)) : 0;
      pushFromPoint(p, c.a.x + abx * t, c.a.z + abz * t, r);
      return;
    }
    case "circle":
      pushFromPoint(p, c.center.x, c.center.z, r + c.radius);
      return;
    case "roundedBox": {
      const sx = p.x >= c.center.x ? 1 : -1;
      const sz = p.z >= c.center.z ? 1 : -1;
      const innerX = c.half.x - c.cornerRadius;
      const innerZ = c.half.z - c.cornerRadius;
      const qx = Math.abs(p.x - c.center.x) - innerX;
      const qz = Math.abs(p.z - c.center.z) - innerZ;
      if (qx > 0 && qz > 0) {
        // Beyond both faces: the nearest feature is a rounded corner.
        pushFromPoint(p, c.center.x + sx * innerX, c.center.z + sz * innerZ, r + c.cornerRadius);
      } else if (qx > qz) {
        const gap = qx - c.cornerRadius; // signed distance to the X face
        if (gap < r) p.x += sx * (r - gap);
      } else {
        const gap = qz - c.cornerRadius;
        if (gap < r) p.z += sz * (r - gap);
      }
      return;
    }
  }
}

/**
 * Move a circle by (dx, dz), sliding along whatever it hits. The move is split into steps no
 * longer than half the radius, so a fast frame can't tunnel through a thin wall.
 */
export function moveCircle(
  p: Vec2,
  dx: number,
  dz: number,
  radius: number,
  colliders: readonly Collider[],
  iterations = 3,
) {
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dz) / (radius * 0.5)));
  for (let s = 0; s < steps; s++) {
    p.x += dx / steps;
    p.z += dz / steps;
    for (let i = 0; i < iterations; i++) for (const c of colliders) pushOut(p, radius, c);
  }
}
