import { AnimationClip, AnimationMixer, LoopOnce, type AnimationAction, type Object3D } from "three";

/**
 * Drives the lift's sliding doors from the GLB's "DoorOpen" clip (both panels, one clip).
 *
 * Open plays the clip forward and close plays it backward from wherever it is, so pressing a
 * button mid-motion turns the doors around. The clip holds its last pose when it finishes.
 */
export class DoorController {
  private readonly mixer: AnimationMixer;
  private readonly action: AnimationAction;
  private readonly panel: Object3D;
  private readonly closedX: number;
  private readonly slideDistance: number;
  private target: "open" | "closed" = "closed";

  constructor(
    private readonly root: Object3D,
    clips: AnimationClip[],
    options: { clip: string; panel: string; slideDistance: number },
  ) {
    const clip = AnimationClip.findByName(clips, options.clip);
    const panel = root.getObjectByName(options.panel);
    if (!clip) throw new Error(`Door clip "${options.clip}" is missing from the GLB`);
    if (!panel) throw new Error(`Door panel "${options.panel}" is missing from the GLB`);

    this.mixer = new AnimationMixer(root);
    this.action = this.mixer.clipAction(clip);
    this.action.setLoop(LoopOnce, 1);
    this.action.clampWhenFinished = true;
    this.panel = panel;
    this.closedX = panel.position.x; // the GLB's rest pose is the closed door
    this.slideDistance = options.slideDistance;
  }

  /** Opening or open, as opposed to closing or closed. */
  get wantsOpen() {
    return this.target === "open";
  }

  /** How far each panel has slid, in metres (0 = closed). */
  get slid() {
    return Math.min(this.slideDistance, Math.abs(this.panel.position.x - this.closedX));
  }

  /** Doors are moving toward closed and haven't got there yet. */
  get isClosing() {
    return this.target === "closed" && this.slid > 1e-3;
  }

  open() {
    this.run("open");
  }

  close() {
    this.run("closed");
  }

  toggle() {
    this.run(this.target === "open" ? "closed" : "open");
  }

  update(dt: number) {
    this.mixer.update(dt);
  }

  dispose() {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.root);
  }

  private run(target: "open" | "closed") {
    if (this.target === target) return;
    this.target = target;
    this.action.paused = false;
    this.action.timeScale = target === "open" ? 1 : -1;
    this.action.play();
  }
}
