// Activity-log line rendering, shared by the Activity card / sheet and the per-test results popup.
import { useEffect, useLayoutEffect, useRef } from "react";
import type { LogEntry } from "../useDemo";
import { fmtTime } from "../util";
import { LogIcon, ShieldIcon, LockIcon } from "./Icons";
import { decodeFailure, edgeFacts, logFacts, type Decoded } from "../teach/decode";
import { useTeach, WhyFix, type TeachInfo } from "./Teach";

type OpenLog = (g: NonNullable<LogEntry["gw"]>) => void;

/** Edge id, or "HR Assistant → HR Records Agent" for an additional orchestrator's edge (CONTRACTS §12). */
function EdgeName({ e }: { e: LogEntry }) {
  return e.edgeLabel ? (
    <span className="log-edge log-edge-agent" title={e.edge}>
      {e.edgeLabel}
    </span>
  ) : (
    <span className="mono log-edge">{e.edge}</span>
  );
}

/** The known failed-call outcome of a log line (edge result, gateway log entry or test error), if any. */
export function decodeEntry(e: LogEntry, t: TeachInfo): Decoded | null {
  if (e.kind === "edge" && e.edgeState) {
    const st = e.edgeState;
    return decodeFailure({ state: st.state, status: st.http_status, detail: st.detail ?? e.text, ...edgeFacts(t.theme, t.active, e.edge) });
  }
  if (e.kind === "gwlog" && e.gw) return decodeFailure(logFacts(e.gw, t.theme, t.active));
  if (e.kind === "error") return decodeFailure({ state: "error", detail: e.text });
  return null;
}

export function LogLine({ e, onOpenLog, why }: { e: LogEntry; onOpenLog?: OpenLog; why?: Decoded | null }) {
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
        {e.kind === "gwlog" && e.edge && <EdgeName e={e} />}
        {e.kind === "gwlog" && e.gw?.private_path && (
          <span className="private-chip" title={`httpRequest.serverIp ${e.gw.server_ip}: a private IP, so the gateway sent this request through your VPC (PSC endpoint)`}>
            <LockIcon size={10} /> private path · {e.gw.server_ip}
          </span>
        )}
        {e.kind === "edge" && (
          <>
            <EdgeName e={e} />
            <span className={`state-pill sp-${s}`}>
              {s === "blocked" && <ShieldIcon size={11} />}
              {s}
              {e.edgeState?.http_status ? ` · ${e.edgeState.http_status}` : ""}
            </span>
            {e.edgeState?.path === "private" && s === "allowed" && (
              <span className="private-chip" title="Through the VPC-enabled gateway, the network attachment and the PSC endpoint">
                <LockIcon size={10} /> private path
              </span>
            )}
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
        {why && <WhyFix d={why} />}
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
  const teach = useTeach();
  // One why / fix per run of identical outcomes (e.g. four denied tools of one server), not on every line.
  let last: string | null = null;
  const whys = log.map((e) => {
    if (e.kind === "user" || e.kind === "done" || e.kind === "system") last = null;
    const w = decodeEntry(e, teach);
    if (!w) return null;
    if (w.code === last) return null;
    last = w.code;
    return w;
  });
  return (
    <div className={`log ${big ? "log-big" : ""} ${className}`} ref={ref} aria-live={(live ?? !big) ? "polite" : "off"} tabIndex={big ? 0 : undefined} aria-label={big || live === false ? label : undefined}>
      {log.length === 0 && <div className="muted log-empty">{empty}</div>}
      {log.map((e, i) => (
        <LogLine key={e.id} e={e} onOpenLog={onOpenLog} why={whys[i]} />
      ))}
    </div>
  );
}
