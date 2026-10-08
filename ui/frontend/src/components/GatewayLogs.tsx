// Agent Gateway request logs from Cloud Logging (CONTRACTS §9): the single-entry viewer and the live "Gateway logs" sheet.
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { Api } from "../api/client";
import type { GatewayLogEntry, GatewayLogsResponse, Theme } from "../api/types";
import type { Demo } from "../useDemo";
import { fmtTime } from "../util";
import { CodeFull } from "./CodeView";
import { CopyButton, GlassModal } from "./Glass";
import { ChevronIcon, LogIcon, OpenInNewIcon, ShieldIcon } from "./Icons";

const rawLines = (e: GatewayLogEntry) => JSON.stringify(e.raw ?? {}, null, 2).split("\n");
const rawText = (e: GatewayLogEntry) => JSON.stringify(e.raw ?? {}, null, 2);

export const GW_SOURCE_NOTE = "Source: Cloud Logging (networkservices.googleapis.com/gateway_requests). The ingress gateway does not write request logs.";

export function DecisionPill({ d }: { d: GatewayLogEntry["decision"] }) {
  return (
    <span className={`gw-pill gw-${d}`}>
      {d === "blocked" && <ShieldIcon size={11} />}
      {d}
    </span>
  );
}

/** Display name of the component a log entry targets, plus the MCP tool when known. */
export function logTarget(theme: Theme | null, e: GatewayLogEntry): { name: string; tool: string | null } {
  const comp = e.component ?? e.edge?.split(":")[0] ?? null;
  const name = (comp && (theme?.mcp_servers[comp]?.display_name ?? theme?.a2a_agents[comp]?.display_name)) || comp || e.host || "unknown";
  return { name, tool: e.mcp_tool ?? (e.edge?.includes(":") ? e.edge.split(":")[1] : null) };
}

/** Glass viewer for one entry: pretty-printed raw JSON, Copy, Open in Cloud Logging, Close. */
export function GatewayLogViewer({ entry, onClose }: { entry: GatewayLogEntry; onClose: () => void }) {
  return (
    <GlassModal
      className="sheet-code sheet-gwentry"
      icon={<LogIcon size={18} />}
      title={entry.summary}
      subtitle={
        <>
          <DecisionPill d={entry.decision} />
          <span className="mono">{entry.edge ?? entry.host}</span>
          <span>· {fmtTime(Date.parse(entry.timestamp))}</span>
          {entry.simulated && <span className="tag tag-sim">Simulated</span>}
        </>
      }
      actions={
        <>
          <CopyButton ariaLabel="Copy the raw log entry JSON" getText={() => rawText(entry)} />
          {!entry.simulated && entry.console_url ? (
            <a className="glass-btn" href={entry.console_url} target="_blank" rel="noopener noreferrer" aria-label="Open this entry in Cloud Logging (new tab)">
              Open in Cloud Logging <OpenInNewIcon size={13} />
            </a>
          ) : (
            <button type="button" className="glass-btn" disabled aria-label="Open in Cloud Logging (not available for simulated entries)" title="Simulated entry: it is not in Cloud Logging">
              Open in Cloud Logging <OpenInNewIcon size={13} />
            </button>
          )}
        </>
      }
      onClose={onClose}
    >
      <p className="sheet-explain">
        {entry.simulated
          ? "Simulated entry, shaped like the one Agent Gateway writes to Cloud Logging for this request."
          : "The Cloud Logging entry Agent Gateway wrote for this request."}{" "}
        {entry.decided_by && (
          <>
            Decided by <span className="mono">{entry.decided_by}</span>.
          </>
        )}
      </p>
      <CodeFull lines={rawLines(entry)} />
    </GlassModal>
  );
}

const SINCE_MS = 30 * 60_000;
const REFRESH_MS = 5000;

/** Live feed of recent gateway decisions for the theme (auto-refresh every 5 s while open). */
export function GatewayLogsSheet({ d, api, onClose }: { d: Demo; api: Api; onClose: () => void }) {
  const [deniedOnly, setDeniedOnly] = useState(true);
  const [resp, setResp] = useState<GatewayLogsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const { themeId, mode, noteGatewayLogs, theme } = d;
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!themeId) return;
    const my = ++seq.current;
    setLoading(true);
    try {
      const r = await api.gatewayLogs(themeId, mode, { since: new Date(Date.now() - SINCE_MS).toISOString(), denied_only: deniedOnly, limit: 100 });
      if (my !== seq.current) return;
      setResp(r);
      setErr(null);
      noteGatewayLogs(r.entries);
    } catch (e) {
      if (my === seq.current) setErr((e as Error).message ?? String(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [api, themeId, mode, deniedOnly, noteGatewayLogs]);

  useEffect(() => {
    void load();
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => clearInterval(iv);
  }, [load]);

  const entries = resp?.entries ?? [];
  const simulated = resp?.source === "simulated";
  return (
    <GlassModal
      className="sheet-log sheet-gwlogs"
      icon={<LogIcon size={18} />}
      title="Gateway logs"
      subtitle={
        <>
          {entries.length} entr{entries.length === 1 ? "y" : "ies"} · last 30 min · {theme?.name}
          {simulated && <span className="tag tag-sim">Simulated</span>}
          <span className="live-dot" title="Refreshes every 5 seconds">
            {loading ? "refreshing" : "live"}
          </span>
        </>
      }
      actions={
        <>
          <button
            type="button"
            role="switch"
            aria-checked={deniedOnly}
            className={`glass-btn gw-toggle ${deniedOnly ? "on" : ""}`}
            onClick={() => setDeniedOnly((v) => !v)}
            aria-label="Show denied and blocked requests only"
            title="Show denied and blocked requests only"
          >
            <span className="gw-toggle-dot" aria-hidden="true" />
            Denied only
          </button>
          {resp?.console_url && (
            <a className="glass-btn" href={resp.console_url} target="_blank" rel="noopener noreferrer" aria-label="Open this query in Cloud Logging (new tab)" title={resp.filter}>
              Open query in Cloud Logging <OpenInNewIcon size={13} />
            </a>
          )}
        </>
      }
      onClose={onClose}
    >
      <p className="gw-note">{GW_SOURCE_NOTE}</p>
      {err && <div className="policy-error">Couldn't load gateway logs: {err}</div>}
      {!resp && !err && <div className="muted">Loading…</div>}
      {resp && entries.length === 0 && (
        <div className="gw-empty muted">
          {deniedOnly ? "No gateway denials in the last 30 minutes." : "No gateway decisions in the last 30 minutes."} Run a test with the gateway attached.
        </div>
      )}
      {entries.length > 0 && (
        <div className="gw-table-wrap">
          <table className="gw-table">
            <thead>
              <tr>
                <th scope="col" className="gw-c-x">
                  <span className="sr-only">Expand</span>
                </th>
                <th scope="col">Time</th>
                <th scope="col">Decision</th>
                <th scope="col">Target</th>
                <th scope="col">Method</th>
                <th scope="col">Status</th>
                <th scope="col">Decided by</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const isOpen = open === e.id;
                const t = logTarget(theme, e);
                const toggle = () => setOpen(isOpen ? null : e.id);
                return (
                  <Fragment key={e.id}>
                    <tr className={`gw-row ${isOpen ? "open" : ""}`} onClick={toggle}>
                      <td className="gw-c-x">
                        <button
                          type="button"
                          className="gw-expand"
                          aria-expanded={isOpen}
                          aria-label={`${isOpen ? "Hide" : "Show"} the raw log entry: ${e.summary}`}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            toggle();
                          }}
                        >
                          <ChevronIcon size={16} />
                        </button>
                      </td>
                      <td className="mono gw-time">{fmtTime(Date.parse(e.timestamp))}</td>
                      <td>
                        <DecisionPill d={e.decision} />
                      </td>
                      <td className="gw-target">
                        {t.name}
                        {t.tool && <span className="mono gw-tool"> · {t.tool}</span>}
                      </td>
                      <td className="mono">{e.mcp_method ?? e.method ?? "—"}</td>
                      <td className="mono">{e.status ?? "—"}</td>
                      <td className="mono gw-by">{e.decided_by ?? "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr className="gw-raw-row">
                        <td colSpan={7}>
                          <div className="gw-raw-tools">
                            <span className="gw-raw-summary">{e.summary}</span>
                            <CopyButton className="mini-btn" ariaLabel="Copy the raw log entry JSON" getText={() => rawText(e)} />
                            {!e.simulated && e.console_url && (
                              <a className="mini-btn" href={e.console_url} target="_blank" rel="noopener noreferrer" aria-label="Open this entry in Cloud Logging (new tab)">
                                Cloud Logging <OpenInNewIcon size={12} />
                              </a>
                            )}
                            {e.simulated && <span className="tag tag-sim">simulated</span>}
                          </div>
                          <CodeFull lines={rawLines(e)} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </GlassModal>
  );
}
