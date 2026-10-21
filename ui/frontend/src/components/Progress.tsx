// Pop-out progress card for a policy / Model Armor change that is propagating in GCP.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fmtClock, fmtDuration } from "../util";
import { effectPhase, effectWindow, type ChangeKind } from "../teach/timing";
import { CheckIcon, ClockIcon } from "./Icons";

const CAP = 0.95;

/** Ticks once a second while mounted. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const iv = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(iv);
  }, [active]);
  return now;
}

/**
 * Tracks pending → settled transitions so a brief success state can be shown.
 * Only fires when the change was visibly pending (> ~1 s), so instant Demo-mode toggles stay quiet.
 */
export function useSettled(status: string | undefined): "applied" | "removed" | null {
  const pending = !!status?.startsWith("pending");
  const startedAt = useRef<number | null>(null);
  const [done, setDone] = useState<"applied" | "removed" | null>(null);
  useEffect(() => {
    if (pending) {
      startedAt.current ??= Date.now();
      setDone(null);
      return;
    }
    if (startedAt.current !== null) {
      const long = Date.now() - startedAt.current > 1200;
      startedAt.current = null;
      if (long && (status === "applied" || status === "removed")) setDone(status);
    }
  }, [pending, status]);
  useEffect(() => {
    if (!done) return;
    const t = window.setTimeout(() => setDone(null), 2400);
    return () => window.clearTimeout(t);
  }, [done]);
  return done;
}

/** The mock's `?typical=N` knob: every change takes N seconds, so each phase of the card can be seen quickly. */
const TYPICAL_OVERRIDE = Number(new URLSearchParams(location.search).get("typical")) || 0;

export function PendingProgress(props: {
  /** ms epoch when the change started (server changed_at, else click time); null = unknown. */
  since: number | null;
  /** What kind of change: picks the measured "takes effect in" window (src/teach/timing.ts). */
  kind: ChangeKind;
  removing: boolean;
  what: string; // "policy" | "Model Armor"
  floating?: boolean;
  id?: string;
}) {
  const [mountedAt] = useState(() => Date.now());
  const now = useNow(true);
  const ref = useRef<HTMLDivElement>(null);
  // Inline pop-outs: bring the card into view in the side panel when a change starts (not on reloads).
  useEffect(() => {
    if (props.floating || (props.since !== null && Date.now() - props.since > 5000)) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const t = window.setTimeout(() => ref.current?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" }), 450);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Floating cards (Model Armor, at the right of the header): keep them inside the viewport.
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!props.floating || !el) return;
    const r = el.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const over = r.right - shift - (vw - 8);
    const under = 8 - (r.left - shift);
    setShift(over > 0 ? -over : under > 0 ? under : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.floating]);
  const since = props.since ?? mountedAt;
  const elapsed = Math.max(0, (now - since) / 1000);
  const win = effectWindow(props.kind, TYPICAL_OVERRIDE);
  const ph = effectPhase(win, elapsed);
  const over = ph.phase === "late";
  const pct = ph.phase === "waiting" ? Math.min(ph.fraction, CAP) * 100 : 100;
  const verb = props.removing ? (props.what === "Model Armor" ? "Disabling" : "Removing") : props.what === "Model Armor" ? "Updating" : "Applying";
  return (
    <div ref={ref} id={props.id} className={`glass progress-pop ${props.floating ? "floating" : "inline"} phase-${ph.phase} ${over ? "over" : ""}`}
      style={shift ? ({ translate: `${shift}px 0`, "--pp-caret": `${-shift}px` } as React.CSSProperties) : undefined}
      role="status" aria-label={`${verb} ${props.what}, propagating in Google Cloud`}>
      <div className="pp-row">
        <span className="mini-spinner pp-spin" aria-hidden="true" />
        <span className="pp-title">
          {verb} {props.what}…
        </span>
        <span className="pp-clock mono" aria-label={`Elapsed ${fmtClock(elapsed)}`} aria-live="off">
          {fmtClock(elapsed)}
        </span>
      </div>
      {ph.phase === "waiting" ? (
        <div className="pp-typical">
          <ClockIcon size={13} />
          <span>
            Takes effect in {win.label}
            {!win.measured && <span className="pp-est"> (estimate)</span>}
          </span>
          <span className="pp-left mono" aria-live="off" aria-label={`about ${fmtClock(ph.left)} left`}>
            {fmtClock(ph.left)} left
          </span>
        </div>
      ) : (
        <div className="pp-typical pp-due">
          <CheckIcon size={13} />
          <span>Should be in effect now.{props.what === "policy" ? " Run a test to check." : ""}</span>
        </div>
      )}
      <div
        className={`pp-bar ${over ? "indeterminate" : ""}`}
        role="progressbar"
        aria-label="Estimated progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={over ? undefined : Math.round(pct)}
      >
        <span className="pp-fill" style={!over ? { width: `${pct}%` } : undefined} />
      </div>
      {over && <div className="pp-note">Taking longer than usual (it can take up to {fmtDuration(win.max)}). GCP propagation times vary; still working.</div>}
    </div>
  );
}

export function SettledPop({ status, what, floating }: { status: "applied" | "removed"; what: string; floating?: boolean }) {
  return (
    <div className={`glass progress-pop settled ${floating ? "floating" : "inline"}`} role="status">
      <div className="pp-row">
        <span className="pp-check" aria-hidden="true">
          <CheckIcon size={14} />
        </span>
        <span className="pp-title">
          {what} {status === "applied" ? (what === "Model Armor" ? "updated" : "applied") : "removed"}
        </span>
      </div>
    </div>
  );
}
