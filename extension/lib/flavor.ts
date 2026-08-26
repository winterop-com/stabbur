// Build flavor. One codebase ships two builds: the neutral "generic" stabbur panel and a
// "dhis2" build branded "stabbur for DHIS2" with DHIS2-oriented copy. The flavor is baked at
// build time via the __HEIM_FLAVOR__ define in wxt.config.ts (driven by the STABBUR_FLAVOR env
// var); nothing forks at runtime beyond a few words — every feature ships in both.

export type Flavor = "generic" | "dhis2";

// Injected by Vite `define` at build time (see wxt.config.ts). Undefined in non-bundled
// contexts (e.g. the screenshots harness), which then reads as the generic flavor.
declare const __HEIM_FLAVOR__: string | undefined;

/** The build flavor baked into this bundle. Defaults to "generic". */
export function flavor(): Flavor {
  return typeof __HEIM_FLAVOR__ !== "undefined" && __HEIM_FLAVOR__ === "dhis2" ? "dhis2" : "generic";
}

/** True when this is the "stabbur for DHIS2" build. */
export function isDhis2Flavor(): boolean {
  return flavor() === "dhis2";
}

/** The panel header / product title for this flavor. */
export function flavorTitle(): string {
  return isDhis2Flavor() ? "stabbur for DHIS2" : "stabbur";
}
