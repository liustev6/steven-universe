import { describe, expect, it } from "vitest";
import { arc, moveCircle, polyline, v2, type Collider } from "@/components/prototype/lobby/collision";
import { doorPanelColliders, lobbyConfig, staticColliders } from "@/components/prototype/lobby/lobbyConfig";

const r = lobbyConfig.player.radius;
const { planeZ: DOOR_Z, roomFaceZ: ROOM_FACE_Z, carFaceZ: CAR_FACE_Z } = lobbyConfig.door;

/** Walk at walking speed in 60 fps steps and return where the player ends up. */
function walk(from: [number, number], dir: [number, number], metres: number, colliders: Collider[] = staticColliders) {
  const p = v2(...from);
  const step = lobbyConfig.player.walkSpeed / 60;
  const n = Math.hypot(...dir);
  for (let t = 0; t < metres / step; t++) moveCircle(p, (dir[0] / n) * step, (dir[1] / n) * step, r, colliders);
  return p;
}

const withDoor = (slid: number) => [...staticColliders, ...doorPanelColliders(slid)];

describe("collision shapes", () => {
  it("stops at a wall segment, one radius away", () => {
    const wall = polyline([v2(-5, 2), v2(5, 2)]);
    expect(walk([0, 0], [0, 1], 5, wall).z).toBeCloseTo(2 - r, 3);
  });

  it("slides along a wall hit at an angle instead of sticking", () => {
    const wall = polyline([v2(-5, 2), v2(5, 2)]);
    const p = walk([0, 0], [1, 1], 4, wall);
    expect(p.z).toBeCloseTo(2 - r, 3);
    expect(p.x).toBeGreaterThan(2); // kept moving along the wall
  });

  it("keeps out of a circle by both radii", () => {
    const pillar: Collider = { kind: "circle", center: v2(0, 2), radius: 0.3 };
    expect(walk([0, 0], [0, 1], 5, [pillar]).z).toBeCloseTo(2 - 0.3 - r, 3);
  });

  it("goes round a rounded box's corner", () => {
    const box: Collider = { kind: "roundedBox", center: v2(0, 0), half: v2(1, 1), cornerRadius: 0.3 };
    const p = walk([2, 2], [-1, -1], 5, [box]);
    const corner = v2(0.7, 0.7); // centre of the corner's rounding
    expect(Math.hypot(p.x - corner.x, p.z - corner.z)).toBeCloseTo(0.3 + r, 3);
  });

  it("doesn't tunnel through a thin wall on a long frame", () => {
    const wall = polyline([v2(-5, 1), v2(5, 1)]);
    const p = v2(0, 0);
    moveCircle(p, 0, 3, r, wall); // 3 m in a single call
    expect(p.z).toBeCloseTo(1 - r, 3);
  });

  it("arc() samples evenly and includes both ends", () => {
    const pts = arc(v2(0, 0), 2, 0, 90, 30);
    expect(pts).toHaveLength(4);
    expect(pts[0].x).toBeCloseTo(2);
    expect(pts[3].z).toBeCloseTo(2);
  });
});

describe("lobby colliders (room-centre coordinates)", () => {
  it("stops at the curved screen-end wall", () => {
    const p = walk([2.5, -3], [0, -1], 10);
    expect(Math.hypot(p.x, p.z + 0.5)).toBeCloseTo(4 - r, 2); // head arc: radius 4 around (0, -0.5)
  });

  it("stops at a straight side wall", () => {
    expect(walk([0, 0], [1, 0], 10).x).toBeCloseTo(3.464 - r, 3);
  });

  it("stops at the reception desk's front", () => {
    expect(walk([0, 0], [0, -1], 5).z).toBeCloseTo(-1.562 + r, 3); // front face at z = -1.562
  });

  it("keeps clear of a corner pillar", () => {
    const p = walk([2.5, 1.5], [1, 1], 5);
    expect(Math.hypot(p.x - 3.354, p.z - 2.436)).toBeCloseTo(0.229 + r, 3);
  });

  it("stops at the wall beside the lift door", () => {
    expect(walk([-1.4, 2], [0, 1], 10, withDoor(1.03)).z).toBeCloseTo(ROOM_FACE_Z - r, 3);
  });

  it("the closed door blocks the doorway", () => {
    expect(walk([0, 2], [0, 1], 10, withDoor(0)).z).toBeCloseTo(DOOR_Z - r, 3);
  });

  it("a door open less than the player's width still blocks", () => {
    expect(walk([0, 2], [0, 1], 10, withDoor(0.2)).z).toBeLessThan(DOOR_Z);
  });

  it("the open door lets the player into the car, up to its back wall", () => {
    expect(walk([0, 2], [0, 1], 10, withDoor(1.03)).z).toBeCloseTo(6.297 - r, 3);
  });

  it("the car's side walls hold the player in", () => {
    expect(walk([0, (CAR_FACE_Z + 6.297) / 2], [1, 0], 5, withDoor(1.03)).x).toBeCloseTo(1.75 - r, 3);
  });

  it("no panel colliders once the door is fully open", () => {
    expect(doorPanelColliders(lobbyConfig.door.slideDistance)).toHaveLength(0);
  });
});
