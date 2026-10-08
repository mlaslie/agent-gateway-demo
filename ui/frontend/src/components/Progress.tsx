// Pop-out progress card for a policy / Model Armor change that is propagating in GCP.
import { useEffect, useRef, useState } from "react";
import { fmtClock, fmtDuration } from "../util";
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

export function PendingProgress(props: {
  /** ms epoch when the change started (server changed_at, else click time); null = unknown. */
  since: number | null;
  typical?: number | null;
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
    const t = window.setTimeout(() => ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 450);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const since = props.since ?? mountedAt;
  const elapsed = Math.max(0, (now - since) / 1000);
  const typical = props.typical && props.typical > 0 ? props.typical : null;
  const ratio = typical ? elapsed / typical : 0;
  const over = typical !== null && ratio > 1;
  const pct = typical ? Math.min(ratio, CAP) * 100 : 0;
  const verb = props.removing ? (props.what === "Model Armor" ? "Disabling" : "Removing") : props.what === "Model Armor" ? "Updating" : "Applying";
  return (
    <div ref={ref} id={props.id} className={`glass progress-pop ${props.floating ? "floating" : "inline"} ${over ? "over" : ""}`} role="status" aria-label={`${verb} ${props.what}, propagating in Google Cloud`}>
      <div className="pp-row">
        <span className="mini-spinner pp-spin" aria-hidden="true" />
        <span className="pp-title">
          {verb} {props.what}…
        </span>
        <span className="pp-clock mono" aria-label={`Elapsed ${fmtClock(elapsed)}`} aria-live="off">
          {fmtClock(elapsed)}
        </span>
      </div>
      {typical !== null && (
        <div className="pp-typical">
          <ClockIcon size={13} /> Typically ~{fmtDuration(typical)}
        </div>
      )}
      <div
        className={`pp-bar ${typical === null || over ? "indeterminate" : ""}`}
        role="progressbar"
        aria-label="Estimated progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={typical !== null ? Math.round(pct) : undefined}
      >
        <span className="pp-fill" style={typical !== null && !over ? { width: `${pct}%` } : undefined} />
      </div>
      {over && <div className="pp-note">Taking longer than usual. GCP propagation times vary; still working.</div>}
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
