import type { Vec2 } from "./collision";

/** What the automation needs from the door (DoorController provides it). */
export interface AutomatedDoor {
  /** Opening or open, as opposed to closing or closed. */
  readonly wantsOpen: boolean;
  readonly isClosing: boolean;
  open(): void;
  close(): void;
}

export type DoorAutomationConfig = {
  /** Half the doorway's width (X). */
  openingHalfWidth: number;
  /** Z of the wall face on the room side, and on the car side, of the doorway. */
  roomFaceZ: number;
  carFaceZ: number;
  /** Z the panels slide along. */
  planeZ: number;
  playerRadius: number;
  /** Walking this close to the doorway opens the door, from the lobby and from the car. */
  openWithinLobbySide: number;
  openWithinCarSide: number;
  /** The door closes once nobody has been that close for this long. */
  closeAfterSeconds: number;
  /** A closing door re-opens if someone is standing in it. */
  reopenWhenObstructed: boolean;
};

/**
 * Lift-door behaviour, like a real lift:
 * - walking up to the doorway (from the lobby or from inside the car) opens it;
 * - once open, it closes after a few seconds with nobody near it, however it was opened;
 * - a closing door re-opens if someone is standing in it.
 *
 * Opening on approach fires when you arrive, not while you stay: if you close the door with a
 * button while standing near it, it stays closed.
 */
export class DoorAutomation {
  private wasNear = false;
  private awaySeconds = 0;

  constructor(private readonly config: DoorAutomationConfig) {}

  /** The player is close enough to the doorway, on either side, to open it. */
  isNear(p: Vec2) {
    const c = this.config;
    return (
      Math.abs(p.x) <= c.openingHalfWidth + c.playerRadius &&
      p.z >= c.roomFaceZ - c.openWithinLobbySide &&
      p.z <= c.carFaceZ + c.openWithinCarSide
    );
  }

  /** The player's body is in the panels' path (with 5 cm to spare), so closing would hit them. */
  isInDoorway(p: Vec2) {
    const c = this.config;
    return Math.abs(p.x) < c.openingHalfWidth && Math.abs(p.z - c.planeZ) < c.playerRadius + 0.05;
  }

  update(dt: number, player: Vec2, door: AutomatedDoor) {
    const near = this.isNear(player);
    if (near && !this.wasNear) door.open();
    this.wasNear = near;

    if (this.config.reopenWhenObstructed && door.isClosing && this.isInDoorway(player)) door.open();

    this.awaySeconds = near || !door.wantsOpen ? 0 : this.awaySeconds + dt;
    if (this.awaySeconds >= this.config.closeAfterSeconds) door.close();
  }
}
