import { describe, expect, it } from "vitest";
import { stickToMove } from "@/components/prototype/lobby/joystick";

const R = 50;
const DZ = 0.2;

describe("stickToMove", () => {
  it("does nothing at rest or inside the dead zone", () => {
    expect(stickToMove(0, 0, R, DZ).move).toEqual({ forward: 0, strafe: 0 });
    expect(stickToMove(0, -9, R, DZ).move).toEqual({ forward: 0, strafe: 0 }); // 0.18 of the radius
  });

  it("pushing up walks forward, right strafes right (screen y points down)", () => {
    expect(stickToMove(0, -R, R, DZ).move.forward).toBeCloseTo(1);
    expect(stickToMove(0, R, R, DZ).move.forward).toBeCloseTo(-1);
    expect(stickToMove(R, 0, R, DZ).move.strafe).toBeCloseTo(1);
  });

  it("speed ramps from the dead zone's edge to full at the rim", () => {
    const half = DZ + (1 - DZ) / 2; // half-way between dead zone and rim
    expect(stickToMove(0, -half * R, R, DZ).move.forward).toBeCloseTo(0.5);
  });

  it("caps speed at the rim and keeps the knob inside it", () => {
    const { move, knob } = stickToMove(3 * R, 4 * R, R, DZ); // 5 radii away, diagonal
    expect(Math.hypot(move.forward, move.strafe)).toBeCloseTo(1);
    expect(Math.hypot(knob.x, knob.y)).toBeCloseTo(R);
    expect(knob.x / knob.y).toBeCloseTo(3 / 4); // same direction as the finger
  });
});
