// DAY / NIGHT: the studio's lighting, switched from the configurator bar.
//
// DAY IS THE FORGE RIG, UNTOUCHED. world.ts is explicit that the viewport lights an asset exactly
// as the contact sheets do, so that a judgement made here holds there. Night therefore never
// replaces the rig: it dims and cools the same four lights, darkens the studio backdrop and turns
// the environment down, and day puts every one of those numbers back exactly as it found them.
//
// It works through the scene graph the world already exposes — the rig is the group named
// "__lights", the backdrop is the studio's own Color object (captured by the editor the moment the
// world is made, before any asset can set a background of its own), and `environmentIntensity`
// is a Scene field (three r163+) that the world's per-frame world swap never writes. Nothing here
// reaches into a private field of EditWorld, and nothing here runs per frame.

export type StudioLook = "day" | "night";

/** What the editor captured from a fresh world: the backdrop Color and its day value. */
export interface StudioRefs {
  /** The studio's own background Color — the same object the world puts back every frame. */
  background: any;
  /** Its day colour, as a hex number. */
  day: number;
}

/** The night rig, one entry per studio light in the rig's own order: key, fill, rim, and the
 *  hemisphere. Moonlight from the key's side, a cold fill, a rim to keep the silhouette. */
export const NIGHT = {
  background: 0x07090f,
  environment: 0.12,
  directional: [
    { color: 0xa9bcff, intensity: 0.42 },   // key: moonlight
    { color: 0x3f4f7a, intensity: 0.18 },   // fill
    { color: 0x7d98ff, intensity: 0.7 },    // rim
  ],
  hemisphere: { color: 0x1c2744, ground: 0x07070b, intensity: 0.3 },
};

interface DayLight { color: number; intensity: number; ground?: number }

/** Put the studio into day or night. Safe to call again with the same look, and on a world with
 *  no rig at all (it then only touches what it finds). Returns how many lights it set. */
export function applyStudioLook(scene: any, look: StudioLook, studio: StudioRefs | null): number {
  if (!scene) return 0;
  const sud = (scene.userData ||= {});
  // Day on a world that has never been night is not a restore, it is nothing: every asset opens
  // through here, and an asset that never touches the switch must not have its rig touched either.
  if (look === "day" && !sud.__studioNight) return 0;
  sud.__studioNight = look === "night";
  const rig = typeof scene.getObjectByName === "function" ? scene.getObjectByName("__lights") : null;
  const lights: any[] = rig?.children ? rig.children.filter((l: any) => l && l.isLight) : [];
  let d = 0;
  let n = 0;
  for (const l of lights) {
    // The day values are read ONCE, the first time the rig is touched, and kept on the light: so
    // night -> night -> day restores the forge rig, not the night before.
    const ud = (l.userData ||= {});
    const day: DayLight = ud.__studioDay || (ud.__studioDay = {
      color: l.color.getHex(), intensity: l.intensity,
      ...(l.groundColor ? { ground: l.groundColor.getHex() } : {}),
    });
    if (look === "day") {
      l.color.setHex(day.color);
      l.intensity = day.intensity;
      if (l.groundColor && day.ground !== undefined) l.groundColor.setHex(day.ground);
    } else if (l.isHemisphereLight) {
      l.color.setHex(NIGHT.hemisphere.color);
      l.intensity = NIGHT.hemisphere.intensity;
      if (l.groundColor) l.groundColor.setHex(NIGHT.hemisphere.ground);
    } else {
      const spec = NIGHT.directional[Math.min(d, NIGHT.directional.length - 1)];
      d++;
      l.color.setHex(spec.color);
      l.intensity = spec.intensity;
    }
    n++;
  }
  if (studio?.background?.isColor) studio.background.setHex(look === "night" ? NIGHT.background : studio.day);
  if ("environmentIntensity" in scene) {
    if (sud.__studioDayEnv === undefined) sud.__studioDayEnv = scene.environmentIntensity;
    scene.environmentIntensity = look === "night" ? NIGHT.environment : sud.__studioDayEnv;
  }
  return n;
}

/** The references to keep from a world the moment it is made, before any asset has run. */
export function captureStudio(scene: any): StudioRefs | null {
  const bg = scene?.background;
  return bg?.isColor ? { background: bg, day: bg.getHex() } : null;
}
