// Activity-log line rendering, shared by the Activity card / sheet and the per-test results popup.
import { useEffect, useLayoutEffect, useRef } from "react";
import type { LogEntry } from "../useDemo";
import { fmtTime } from "../util";
import { LogIcon, ShieldIcon } from "./Icons";

type OpenLog = (g: NonNullable<LogEntry["gw"]>) => void;

export function LogLine({ e, onOpenLog }: { e: LogEntry; onOpenLog?: OpenLog }) {
  const s = e.edgeState?.state;
  return (
    <div className={`log-line log-${e.kind}`}>
      <span className="log-time">{fmtTime(e.ts)}</span>
      <div className="log-body">
        {e.kind === "agent" && <span className="log-role role-agent">Agent</span>}
        {e.kind === "tool" && <span className="log-role role-tool">Tool</span>}
        {e.kind === "user" && <span className="log-role role-user">Prompt</span>}
        {e.kind === "fallback" && <span className="log-role role-fallback">Fallback</span>}
        {e.kind === "error" && <span className="log-role role-error">Error</span>}
        {e.kind === "ge" && <span className="log-role role-ge">GE</span>}
        {e.kind === "gwlog" && e.gw && (
          <span className={`log-role role-gwlog gwl-${e.gw.decision}`} title="Agent Gateway request log (Cloud Logging)">
            <LogIcon size={11} /> Gateway log
          </span>
        )}
        {e.kind === "gwlog" && e.edge && <span className="mono log-edge">{e.edge}</span>}
        {e.kind === "edge" && (
          <>
            <span className="mono log-edge">{e.edge}</span>
            <span className={`state-pill sp-${s}`}>
              {s === "blocked" && <ShieldIcon size={11} />}
              {s}
              {e.edgeState?.http_status ? ` · ${e.edgeState.http_status}` : ""}
            </span>
          </>
        )}
        {e.replayed && <span className="replayed">replayed</span>}
        {e.kind === "gwlog" && e.gw && onOpenLog ? (
          <button type="button" className="log-text gw-open" onClick={() => onOpenLog(e.gw!)} aria-label={`View the raw log entry: ${e.text}`} title="View the raw log entry">
            {e.text}
          </button>
        ) : (
          <span className="log-text">{e.text}</span>
        )}
        {e.kind === "gwlog" && e.gw && (
          e.gw.simulated ? (
            <span className="gw-sim-note">simulated</span>
          ) : e.gw.console_url ? (
            <a className="gw-cl-link" href={e.gw.console_url} target="_blank" rel="noopener noreferrer" aria-label="Open this log entry in Cloud Logging (new tab)">
              Open in Cloud Logging ↗
            </a>
          ) : null
        )}
      </div>
    </div>
  );
}

/** Scrolls to the bottom when entries arrive, unless the reader scrolled up. */
export function useStickyBottom(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return ref;
}

export function LogList({
  log,
  big,
  onOpenLog,
  label = "Activity log",
  empty = "Run a test to see the agent's calls stream in here.",
  className = "",
  live,
}: {
  log: LogEntry[];
  big?: boolean;
  onOpenLog?: OpenLog;
  label?: string;
  empty?: string;
  className?: string;
  /** aria-live polite (default: on for the small list, off for the expanded one). */
  live?: boolean;
}) {
  const ref = useStickyBottom(log);
  return (
    <div className={`log ${big ? "log-big" : ""} ${className}`} ref={ref} aria-live={(live ?? !big) ? "polite" : "off"} tabIndex={big ? 0 : undefined} aria-label={big || live === false ? label : undefined}>
      {log.length === 0 && <div className="muted log-empty">{empty}</div>}
      {log.map((e) => (
        <LogLine key={e.id} e={e} onOpenLog={onOpenLog} />
      ))}
    </div>
  );
}
