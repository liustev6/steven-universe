import { beforeEach, describe, expect, it } from "vitest";
import { DoorAutomation, type AutomatedDoor } from "@/components/prototype/lobby/doorAutomation";
import { lobbyConfig } from "@/components/prototype/lobby/lobbyConfig";

// Distances and timings come from the config, so these tests follow any retuning.
const { door: doorConfig, player } = lobbyConfig;
const {
  openWithinLobbySide: ZONE,
  openWithinCarSide: CAR_ZONE,
  closeAfterSeconds: CLOSE_AFTER,
  roomFaceZ,
  carFaceZ,
  planeZ,
} = doorConfig;
const NEAR_LOBBY_SIDE = roomFaceZ - ZONE + 0.05; // just inside the zone, in the lobby
const AWAY_IN_LOBBY = roomFaceZ - ZONE - 1.0;
const CAR_BACK = 6.0; // the car's back wall is at 6.297
const OPEN_TIME = 1.2; // the fake door takes 1 s

/** A stand-in for the real door: takes 1 s to fully open or close. */
class FakeDoor implements AutomatedDoor {
  wantsOpen = false;
  openness = 0;
  get isClosing() {
    return !this.wantsOpen && this.openness > 0;
  }
  open() {
    this.wantsOpen = true;
  }
  close() {
    this.wantsOpen = false;
  }
  step(dt: number) {
    this.openness = Math.min(1, Math.max(0, this.openness + (this.wantsOpen ? dt : -dt)));
  }
  get state() {
    if (this.wantsOpen) return this.openness >= 1 ? "open" : "opening";
    return this.openness <= 0 ? "closed" : "closing";
  }
}

let automation: DoorAutomation;
let door: FakeDoor;

/** Stand at (x, z) for `seconds`, at 60 fps. */
function stand(seconds: number, z: number, x = 0) {
  const dt = 1 / 60;
  for (let i = 0; i < Math.round(seconds / dt); i++) {
    automation.update(dt, { x, z }, door);
    door.step(dt);
  }
}

beforeEach(() => {
  automation = new DoorAutomation({ ...doorConfig, playerRadius: player.radius });
  door = new FakeDoor();
});

describe("opening on approach", () => {
  it("stays closed while the player is elsewhere in the lobby", () => {
    stand(5, AWAY_IN_LOBBY);
    expect(door.state).toBe("closed");
  });

  it("doesn't open just outside the zone", () => {
    stand(2, roomFaceZ - ZONE - 0.05);
    expect(door.state).toBe("closed");
  });

  it("opens when the player comes into the zone on the lobby side", () => {
    stand(1, AWAY_IN_LOBBY);
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    expect(door.state).toBe("open");
  });

  it("opens when the player walks up to it from inside the car", () => {
    stand(1, CAR_BACK);
    stand(OPEN_TIME, carFaceZ + CAR_ZONE - 0.05);
    expect(door.state).toBe("open");
  });

  it("the car's back half is out of the zone, so the door can close behind you", () => {
    expect(CAR_BACK).toBeGreaterThan(carFaceZ + CAR_ZONE);
  });

  it("counts standing in front of the jambs, but not well off to the side", () => {
    stand(3, NEAR_LOBBY_SIDE, 1.6);
    expect(door.state).toBe("closed");
    stand(OPEN_TIME, NEAR_LOBBY_SIDE, 1.2);
    expect(door.state).toBe("open");
  });
});

describe(`closing after ${CLOSE_AFTER} s away`, () => {
  it("stays open while the player stays near", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    stand(10, NEAR_LOBBY_SIDE);
    expect(door.state).toBe("open");
  });

  it("closes once the player has been away long enough, not before", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    stand(CLOSE_AFTER - 0.2, AWAY_IN_LOBBY);
    expect(door.state).toBe("open");
    stand(0.3, AWAY_IN_LOBBY);
    expect(door.state).toBe("closing");
    stand(1, AWAY_IN_LOBBY);
    expect(door.state).toBe("closed");
  });

  it("closes after a button opens it if nobody comes near", () => {
    door.open(); // pressed from up to 3 m away
    stand(CLOSE_AFTER - 0.1, AWAY_IN_LOBBY);
    expect(door.wantsOpen).toBe(true);
    stand(0.2, AWAY_IN_LOBBY);
    expect(door.state).toBe("closing");
  });

  it("closes behind a player who goes to the back of the car", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    stand(CLOSE_AFTER + 0.2, CAR_BACK);
    expect(door.state).toBe("closing");
  });
});

describe("buttons and the safety sensor", () => {
  it("a close button pressed while standing near is obeyed (no re-open)", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    door.close();
    stand(4, NEAR_LOBBY_SIDE);
    expect(door.state).toBe("closed");
  });

  it("walking away and back opens it again", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    door.close();
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    stand(0.2, AWAY_IN_LOBBY);
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    expect(door.state).toBe("open");
  });

  it("a closing door re-opens for someone standing in the doorway", () => {
    stand(OPEN_TIME, NEAR_LOBBY_SIDE);
    stand(CLOSE_AFTER + 0.5, AWAY_IN_LOBBY); // closing by now
    expect(door.state).toBe("closing");
    stand(0.05, planeZ);
    expect(door.state).toBe("opening");
    stand(5, planeZ);
    expect(door.state).toBe("open");
  });
});
