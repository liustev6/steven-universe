/** Walking input from the touch joystick: each -1..1, together never longer than 1. */
export type MoveInput = { forward: number; strafe: number };

export const NO_MOVE: MoveInput = { forward: 0, strafe: 0 };

/**
 * Turns the finger's offset from the stick's centre (screen pixels, y pointing down) into
 * walking input, and where to draw the knob.
 * - Pushing to the rim walks at full speed, half-way walks at half speed.
 * - A small dead zone in the middle ignores a resting thumb; speed ramps up from its edge.
 * - The knob stays inside the rim however far the finger goes.
 */
export function stickToMove(
  dx: number,
  dy: number,
  radius: number,
  deadzone: number,
): { move: MoveInput; knob: { x: number; y: number } } {
  const distance = Math.hypot(dx, dy);
  if (distance === 0) return { move: NO_MOVE, knob: { x: 0, y: 0 } };

  const toRim = Math.min(1, radius / distance);
  const knob = { x: dx * toRim, y: dy * toRim };

  const push = Math.min(1, distance / radius);
  if (push <= deadzone) return { move: NO_MOVE, knob };
  const speed = (push - deadzone) / (1 - deadzone);
  return { move: { forward: (-dy / distance) * speed, strafe: (dx / distance) * speed }, knob };
}
