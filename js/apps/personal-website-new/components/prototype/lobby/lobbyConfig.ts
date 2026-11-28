import { arc, polyline, v2, type Collider, type Vec2 } from "./collision";

/**
 * Everything tunable about the lobby, in one place.
 *
 * Coordinates are three.js metres with the ROOM CENTRE AT THE ORIGIN (the GLBs are offset by
 * `worldOffset` to get there): +Y up, the screen and desk toward -Z, the lift toward +Z.
 * From Blender (Z up): x = blenderX + 7.3, z = 8.0 - blenderY.
 */

// --- Room geometry (from website.blend; see HANDOFF.md §3, §5) -------------------------------
const R = 4.0; // stadium arc radius
const HEAD = v2(0, -0.5); // arc centre at the screen end
const TAIL = v2(0, 0.5); // arc centre at the lift end
const BAY_FACE_Z = 4.097; // room face of the flat wall the lift door sits in
const BAY_BACK_Z = 4.297; // lift-side face of that wall (0.2 m thick)
const CAR_BACK_Z = 6.297; // the car's back wall, where the control screen is
const CAR_HALF_W = 1.75; // car interior is 3.5 m wide (also the flat wall's half-width)
const OPENING_HALF_W = 1.0; // the doorway is 2.0 m wide
const DOOR_PLANE_Z = 4.197; // where the sliding panels run, mid-wall

/** Angle on the tail arc where the curved wall meets the flat door bay. */
const BAY_ANGLE = (Math.asin((BAY_FACE_Z - TAIL.z) / R) * 180) / Math.PI;

export type InteractionAction =
  | { type: "door"; command: "open" | "close" | "toggle" };

export type Interactable = {
  action: InteractionAction;
  label: string;
  /**
   * Other objects that also press this one, e.g. the whole plate around a small button. Hovering
   * them highlights this object.
   */
  hitArea?: string[];
};

export type AreaLightConfig = {
  name: string;
  position: [number, number, number];
  /** The light faces this point. */
  target: [number, number, number];
  width: number;
  height: number;
  /** Power as set in Blender; converted to three.js luminance. */
  blenderWatts: number;
  /** Light colour as set in Blender (linear RGB). */
  color: [number, number, number];
};

export const lobbyConfig = {
  assets: {
    shell: "/prototype/lobby/shell.glb",
    floor: "/prototype/lobby/floor_1.glb",
  },
  /** Moves the Blender-exported scene so the room centre sits at the origin. */
  worldOffset: [7.3, 0, 8.0] as [number, number, number],

  camera: { fov: 60, near: 0.05, far: 60 },

  player: {
    spawn: v2(0, 1.0),
    /** Radians about +Y; Math.PI faces the lift (+Z), 0 faces the desk and screen. */
    spawnYaw: Math.PI,
    eyeHeight: 1.6,
    radius: 0.25,
    walkSpeed: 2.2, // m/s
    mouseSensitivity: 0.0022, // radians per pixel of mouse movement, while the mouse is captured
    touchSensitivity: 0.006, // radians per dragged pixel, on touch screens (a full-width swipe on a phone turns ~250°)
    maxPitch: (75 * Math.PI) / 180,
  },

  render: {
    /**
     * Most pixels rendered per screen pixel (capped by the screen's own density). Phones get less:
     * every pixel is lit by every light, so 2x on a phone is ~1M lit pixels a frame.
     * If the frame rate drops, both fall back to `lowered` until it recovers.
     */
    resolution: { mouse: 2, touch: 1.5, lowered: 1 },
    exposure: 1.0,
    background: "#0b0c0e",
    /**
     * glTF stores lights in photometric units (candela), Blender works in watts. Scaling by 1/683
     * gives three.js the same numbers Blender renders with, so the GLB's spots match Blender.
     */
    glbLightScale: 1 / 683,
    /**
     * The six ceiling downlights in the GLB. Off makes them decorative: their discs still glow but
     * they light nothing, which saves about a third of the per-pixel lighting cost on phones.
     */
    glbLights: false,
    /**
     * Stands in for the light Blender bounces around the room (no baking yet). It's a hemisphere
     * light: surfaces facing down (the ceiling) get `fromBelow`, the light bounced off the floor;
     * surfaces facing up get `fromAbove`; walls get a mix. Tuned against Blender renders.
     */
    bounceFill: { fromAbove: 0.05, fromBelow: 0.3, color: "#fff4e8" },
    /** A neutral studio environment, mainly for reflections on the satin surfaces. */
    environmentIntensity: 0.08,
    /**
     * glTF has no area lights, so the two from Blender are rebuilt here as RectAreaLights.
     * Drop these once the lighting is baked into lightmaps.
     */
    areaLights: [
      {
        name: "Ring light area", // 2.2 m disk in Blender: an equal-area square
        position: [0, 2.84, 0],
        target: [0, 0, 0],
        width: 1.95,
        height: 1.95,
        blenderWatts: 448,
        color: [1, 0.98, 0.95],
      },
      {
        name: "Elevator light",
        position: [0, 2.55, (BAY_BACK_Z + CAR_BACK_Z) / 2],
        target: [0, 0, (BAY_BACK_Z + CAR_BACK_Z) / 2],
        width: 3.0,
        height: 1.8,
        blenderWatts: 136,
        color: [1, 0.97, 0.92],
      },
    ] satisfies AreaLightConfig[],
  },

  door: {
    clip: "DoorOpen",
    /** Panel whose travel is measured to know how far the door is open. */
    panel: "Door_Left",
    /** How far each panel slides into its pocket. */
    slideDistance: 1.03,
    planeZ: DOOR_PLANE_Z,
    openingHalfWidth: OPENING_HALF_W,
    roomFaceZ: BAY_FACE_Z,
    carFaceZ: BAY_BACK_Z,
    /**
     * Walking this close to the doorway opens the door, measured from the player's centre to the
     * wall face on that side (the closest anyone can stand is 0.15 m from it). The car side is
     * shorter: the car is only 2 m deep, and a zone covering all of it would keep the door open
     * as long as anyone is inside. At 1 m, the back half of the car lets it close behind you.
     */
    openWithinLobbySide: 1.75,
    openWithinCarSide: 1.0,
    /** Once open, the door closes after this long with nobody that close. */
    closeAfterSeconds: 1.5,
    /** Like a real lift: a closing door re-opens if someone is standing in it. */
    reopenWhenObstructed: true,
  },

  /** Clickable objects by node name (as three.js sees it: spaces become underscores). */
  interactables: {
    Door_Button: {
      action: { type: "door", command: "toggle" },
      label: "Call the lift",
      hitArea: ["Door_control_panel"], // the whole plate around the button
    },
    Door_Open_Btn: { action: { type: "door", command: "open" }, label: "Open doors" },
    Door_Close_Btn: { action: { type: "door", command: "close" }, label: "Close doors" },
  } satisfies Record<string, Interactable>,
  interaction: {
    /** Buttons further away than this ignore clicks (metres from the eye). */
    maxReach: 3.0,
    /** Emissive multiplier while the cursor is over an interactable. */
    hoverGlow: 2.2,
    /** Pointer travel (px) that turns a click into a drag-to-look. */
    clickTolerancePx: 6,
  },

  touch: {
    /**
     * Diameter of the on-screen walking stick. It follows the screen's shorter side (30% of it),
     * from 96 px on small phones up to 160 px on tablets.
     */
    joystickSize: "clamp(96px, 30vmin, 160px)",
    /** Share of the radius around the centre that a resting thumb can sit in without walking. */
    deadzone: 0.15,
  },

  debug: {
    /** Draw the colliders on the floor. Also on with ?colliders in the URL. */
    showColliders: false,
  },
};

const wall = (from: Vec2, to: Vec2) => polyline([from, to]);

/**
 * The walls and obstacles the player can't walk through. The door panels are added per frame
 * by the scene, from how far the door is open.
 */
export const staticColliders: Collider[] = [
  // Room outline, from the right jamb of the doorway all the way round to the left jamb.
  ...polyline([
    v2(OPENING_HALF_W, BAY_FACE_Z),
    ...arc(TAIL, R, BAY_ANGLE, 30), // lift end, right of the bay
    ...arc(HEAD, R, -30, -150), // screen end (the straight side walls join these arcs)
    ...arc(TAIL, R, 150, 180 - BAY_ANGLE), // lift end, left of the bay
    v2(-OPENING_HALF_W, BAY_FACE_Z),
  ]),
  // Doorway jambs through the 0.2 m wall.
  ...wall(v2(OPENING_HALF_W, BAY_FACE_Z), v2(OPENING_HALF_W, BAY_BACK_Z)),
  ...wall(v2(-OPENING_HALF_W, BAY_FACE_Z), v2(-OPENING_HALF_W, BAY_BACK_Z)),
  // Lift car.
  ...polyline([
    v2(OPENING_HALF_W, BAY_BACK_Z),
    v2(CAR_HALF_W, BAY_BACK_Z),
    v2(CAR_HALF_W, CAR_BACK_Z),
    v2(-CAR_HALF_W, CAR_BACK_Z),
    v2(-CAR_HALF_W, BAY_BACK_Z),
    v2(-OPENING_HALF_W, BAY_BACK_Z),
  ]),
  // Corner pillars (plinth radius).
  ...[v2(3.354, -2.436), v2(-3.354, -2.436), v2(-3.354, 2.436), v2(3.354, 2.436)].map(
    (center): Collider => ({ kind: "circle", center, radius: 0.229 }),
  ),
  // Reception desk.
  { kind: "roundedBox", center: v2(0, -1.95), half: v2(1.888, 0.388), cornerRadius: 0.3 },
];

/** The parts of the doorway still covered by the panels, given how far they've slid. */
export function doorPanelColliders(slid: number): Collider[] {
  const { planeZ: z, openingHalfWidth: w } = lobbyConfig.door;
  if (slid >= w) return [];
  return [...wall(v2(-w, z), v2(-slid, z)), ...wall(v2(slid, z), v2(w, z))];
}
