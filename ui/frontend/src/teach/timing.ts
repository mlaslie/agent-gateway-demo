// How long a Live policy change takes to take effect, and where a running change is in that window.
// Measured in this project (docs/ISSUES.md #4, #6, #13; docs/TROUBLESHOOTING.md quick map). Used by the pending
// progress card (components/Progress.tsx). Pure module, so `node --test` can load it: see timing.test.ts.

export type ChangeKind = "grant" | "bind" | "enforce" | "model_armor";

export interface EffectWindow {
  kind: ChangeKind;
  /** Usual time to take effect (s): the countdown target. */
  typical: number;
  /** Longest seen (s): past this it's "taking longer than usual". */
  max: number;
  /** "about 2 min" */
  label: string;
  /** false: an estimate, not measured. */
  measured: boolean;
}

export const EFFECT_WINDOWS: Record<ChangeKind, EffectWindow> = {
  // roles/iap.egressor grant or revoke: about 2 min, up to about 7.
  grant: { kind: "grant", typical: 120, max: 420, label: "about 2 min", measured: true },
  // Gateway bind / unbind: the engine PATCH redeploys the container, about 5 min (pending for up to 10).
  bind: { kind: "bind", typical: 300, max: 600, label: "about 5 min", measured: true },
  // Enforce / dry run flip on the IAP authz extension: about 2–3 min.
  enforce: { kind: "enforce", typical: 150, max: 180, label: "about 2–3 min", measured: true },
  // Model Armor on / off: a few minutes (not measured as a separate step).
  model_armor: { kind: "model_armor", typical: 240, max: 600, label: "a few minutes", measured: false },
};

/** Change kind of a policy type (themes.py PolicyType), or of the Model Armor / enforcement switches. */
export function changeKind(type: string): ChangeKind {
  if (type === "gateway_attach") return "bind";
  if (type === "model_armor") return "model_armor";
  if (type === "enforcement" || type === "dry_run" || type === "enforce") return "enforce";
  return "grant"; // a2a_allow, mcp_server_allow, mcp_tool_allow
}

/** "about 2 min" / "about 1½ min" / "about 45 s". */
export function aboutLabel(seconds: number): string {
  const s = Math.max(1, Math.round(seconds));
  if (s < 60) return `about ${s} s`;
  const halves = Math.round(s / 30);
  const whole = Math.floor(halves / 2);
  return `about ${whole}${halves % 2 ? "½" : ""} min`;
}

/**
 * The window for a change kind. `overrideS` (> 0) replaces the measured time, for the mock's `?typical=N` knob
 * that makes every phase visible in seconds; max is then twice that.
 */
export function effectWindow(kind: ChangeKind, overrideS?: number | null): EffectWindow {
  const w = EFFECT_WINDOWS[kind];
  if (!overrideS || overrideS <= 0) return w;
  return { ...w, typical: overrideS, max: overrideS * 2, label: aboutLabel(overrideS) };
}

export type EffectPhase =
  /** Still inside the usual time: `left` seconds of the countdown remain. */
  | { phase: "waiting"; left: number; fraction: number }
  /** Past the usual time: should be in effect now (not yet confirmed). */
  | { phase: "due" }
  /** Past the longest seen time. */
  | { phase: "late" };

export function effectPhase(w: EffectWindow, elapsedS: number): EffectPhase {
  const e = Math.max(0, elapsedS);
  if (e < w.typical) return { phase: "waiting", left: Math.ceil(w.typical - e), fraction: e / w.typical };
  if (e < w.max) return { phase: "due" };
  return { phase: "late" };
}
