import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Api } from "../api/client";
import type { Policy, PolicyStatus } from "../api/types";
import type { Demo, LogEntry } from "../useDemo";
import { fmtTime, logToText } from "../util";
import { CodeFull, CodeInline } from "./CodeView";
import { CopyButton, GlassModal } from "./Glass";
import { GatewayLogsSheet } from "./GatewayLogs";
import { ChevronIcon, ClockIcon, CloseIcon, CopyIcon, ExpandIcon, EyeIcon, LogIcon, OpenInNewIcon, PlayIcon, RefreshIcon, ShieldIcon, SparkIcon, WarnIcon } from "./Icons";
import { PendingProgress, SettledPop, useSettled } from "./Progress";

const STATUS_TEXT: Record<string, string> = {
  applied: "Applied",
  removed: "Not applied",
  pending: "Pending",
  pending_removal: "Removing",
  error: "Error",
};

function Pill({ st, settled }: { st?: PolicyStatus; settled?: boolean }) {
  const s = st?.status ?? "removed";
  return (
    <span className={`pill pill-${s} ${settled ? "pill-settled" : ""}`} title={st?.detail || undefined}>
      {s.startsWith("pending") && <span className="mini-spinner" />}
      {STATUS_TEXT[s] ?? s}
    </span>
  );
}

function Switch({ checked, disabled, onChange, title, describedBy }: { checked: boolean; disabled?: boolean; onChange: (v: boolean) => void; title?: string; describedBy?: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={title}
      aria-describedby={describedBy}
      className={`switch ${checked ? "on" : ""}`}
      disabled={disabled}
      title={title}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" />
    </button>
  );
}

function PolicyCard({ d, api, policy }: { d: Demo; api: Api; policy: Policy }) {
  const st = d.state?.policies[policy.id];
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [lines, setLines] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if ((!open && !expanded) || lines || !d.themeId) return;
    api
      .explain(d.themeId, policy.id)
      .then((r) => setLines(r.lines.filter((l) => l.trim() !== policy.explain?.trim()))) // backend may echo policy.explain as line 1
      .catch((e) => setErr(e.message ?? String(e)));
  }, [open, expanded, lines, api, d.themeId, policy.id, policy.explain]);
  const applied = !!st?.applied;
  const pending = !!st?.status?.startsWith("pending");
  const removing = st?.status === "pending_removal";
  const settled = useSettled(st?.status);
  const popId = `pp-${policy.id}`;
  const allText = () => [policy.explain, ...(lines ?? [])].filter(Boolean).join("\n");
  return (
    <div className={`policy ${applied ? "is-applied" : ""} ${pending ? "is-pending" : ""} ${st?.status === "error" ? "is-error" : ""}`}>
      <div className="policy-main">
        <Switch
          checked={applied}
          disabled={!d.canAdmin || !d.state}
          title={!d.canAdmin ? "Read-only viewer — only presenters (admins) can change policies" : applied ? `Remove policy: ${policy.text}` : `Apply policy: ${policy.text}`}
          describedBy={pending ? popId : undefined}
          onChange={(v) => d.setPolicy(policy.id, v ? "apply" : "remove")}
        />
        <div className="policy-text">
          {policy.text}
        </div>
        <Pill st={st} settled={!!settled} />
      </div>
      {pending && (
        <PendingProgress
          id={popId}
          what="policy"
          removing={removing}
          since={d.pendingSince(policy.id, st?.changed_at)}
          typical={removing ? policy.typical_remove_seconds : policy.typical_seconds}
        />
      )}
      {!pending && settled && <SettledPop status={settled} what="Policy" />}
      {st?.status === "error" && st.detail && <div className="policy-error">{st.detail}</div>}
      <div className="hood-bar">
        <button className={`hood-toggle ${open ? "open" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
          <ChevronIcon size={16} /> Under the hood <span className="mono hood-type">{policy.type}</span>
        </button>
        {open && (
          <div className="hood-tools" role="toolbar" aria-label="Under the hood actions">
            <button className="mini-btn" onClick={() => setExpanded(true)} aria-label={`Expand the commands for ${policy.text}`} title="Expand">
              <ExpandIcon size={13} /> Expand
            </button>
            <CopyButton className="mini-btn" ariaLabel={`Copy the commands for ${policy.text}`} getText={allText} />
            <button className="mini-btn" onClick={() => setOpen(false)} aria-label="Close under the hood" title="Close">
              <CloseIcon size={13} /> Close
            </button>
          </div>
        )}
      </div>
      {open && (
        <div className="hood">
          {policy.explain && <p className="hood-explain">{policy.explain}</p>}
          {err && <div className="policy-error">{err}</div>}
          {!lines && !err && <div className="muted">Loading…</div>}
          {lines && lines.length > 0 && <CodeInline lines={lines} />}
          {lines && lines.length === 0 && !policy.explain && <div className="muted">No details available.</div>}
        </div>
      )}
      {expanded && (
        <GlassModal
          className="sheet-code"
          icon={<ShieldIcon size={18} />}
          title="Under the hood"
          subtitle={
            <>
              {policy.text} <span className="mono hood-type">{policy.type}</span>
            </>
          }
          actions={<CopyButton ariaLabel={`Copy all commands for ${policy.text}`} getText={allText} />}
          onClose={() => setExpanded(false)}
        >
          {policy.explain && <p className="sheet-explain">{policy.explain}</p>}
          {err && <div className="policy-error">{err}</div>}
          {!lines && !err && <div className="muted">Loading…</div>}
          {lines && lines.length > 0 && <CodeFull lines={lines} />}
          {lines && lines.length === 0 && !policy.explain && <div className="muted">No details available.</div>}
        </GlassModal>
      )}
    </div>
  );
}

function LogLine({ e, onOpenLog }: { e: LogEntry; onOpenLog?: (g: NonNullable<LogEntry["gw"]>) => void }) {
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
function useStickyBottom(dep: unknown) {
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

function LogList({ log, big, onOpenLog }: { log: LogEntry[]; big?: boolean; onOpenLog?: (g: NonNullable<LogEntry["gw"]>) => void }) {
  const ref = useStickyBottom(log);
  return (
    <div className={`log ${big ? "log-big" : ""}`} ref={ref} aria-live={big ? "off" : "polite"} tabIndex={big ? 0 : undefined} aria-label={big ? "Activity log" : undefined}>
      {log.length === 0 && <div className="muted log-empty">Run a test to see the agent's calls stream in here.</div>}
      {log.map((e) => (
        <LogLine key={e.id} e={e} onOpenLog={onOpenLog} />
      ))}
    </div>
  );
}

export function SidePanel({ d, api }: { d: Demo; api: Api }) {
  const sc = d.scenario;
  const theme = d.theme;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [logExpanded, setLogExpanded] = useState(false);
  const [gwOpen, setGwOpen] = useState(false);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [sc?.id]);
  if (!sc || !theme) return <aside className="side" />;
  const missingPre = sc.preconditions.filter((pid) => !d.state?.policies[pid]?.applied);
  const prePolicies = sc.preconditions.map((pid) => theme.policies.find((p) => p.id === pid)).filter(Boolean) as Policy[];
  const policies = sc.policies.map((pid) => theme.policies.find((p) => p.id === pid)).filter(Boolean) as Policy[];
  const isDemo = d.mode === "demo";
  const ge = d.geDemo;
  const geUrl = d.config?.gemini_enterprise?.app_url || "";
  const isIngress = sc.flow === "ingress" || sc.tests.some((t) => t.probes.some((p) => p.edge.startsWith("ingress:")));
  // Ingress (CLIENT_TO_AGENT) only screens content with Model Armor: nudge the presenter to turn it on.
  const maOff = !!d.state && !d.state.model_armor.enabled;
  // Recording always runs against Live GCP (backend: Live only, admin only). Hidden in GE Demo.
  const showRecord = !ge && !isDemo && d.canAdmin && !!d.config?.live_available;
  return (
    <aside className="side">
      <div className="side-scroll" ref={scrollRef}>
        <section className="card scenario-card">
          <div className="eyebrow">
            Scenario {sc.order} · {sc.flow === "ingress" ? "Ingress" : "Egress"}
          </div>
          <h2 className="scenario-title">{sc.title}</h2>
          {sc.subtitle && <div className="scenario-sub">{sc.subtitle}</div>}
          {sc.description && <p className="scenario-desc">{sc.description}</p>}
        </section>

        {prePolicies.length > 0 && (
          <section className={`card precond ${missingPre.length ? "missing" : "ok"}`}>
            <div className="precond-head">
              {missingPre.length ? <WarnIcon size={18} /> : <ShieldIcon size={18} />}
              <span>{missingPre.length ? "This scenario starts from:" : "Starting point in place:"}</span>
            </div>
            <ul className="precond-list">
              {prePolicies.map((p) => {
                const st = d.state?.policies[p.id];
                return (
                  <li key={p.id}>
                    <span className="precond-text">{p.text}</span>
                    <Pill st={st} />
                  </li>
                );
              })}
            </ul>
            {missingPre.length > 0 && (
              <button className="btn btn-primary btn-sm" disabled={!d.canAdmin || !d.state} title={d.canAdmin ? undefined : "Read-only viewer"} onClick={() => d.applyPreconditions()}>
                Apply preconditions
              </button>
            )}
          </section>
        )}

        <section className="card">
          <div className="card-head">
            <h3>Policies</h3>
            <span className="muted small">{isDemo ? "simulated" : "real GCP changes"}</span>
          </div>
          {policies.length === 0 && <div className="muted">No policies for this scenario.</div>}
          {policies.map((p) => (
            <PolicyCard key={p.id} d={d} api={api} policy={p} />
          ))}
        </section>

        {isIngress && maOff && (
          <section className="card ma-callout" role="note">
            <ShieldIcon size={18} />
            <div>
              <div>Turn on Model Armor (top right) to screen prompts at the ingress gateway.</div>
              <button
                className="btn btn-sm ma-callout-btn"
                disabled={!d.canAdmin}
                title={d.canAdmin ? "Enable Model Armor on the gateways (all themes)" : "Read-only viewer"}
                onClick={() => d.setModelArmor(true)}
              >
                Turn on Model Armor
              </button>
            </div>
          </section>
        )}

        <section className={`card ${ge ? "tests-ge" : ""}`}>
          <div className="card-head">
            <h3>Tests</h3>
            {ge ? (
              geUrl ? (
                <a className="ge-link" href={geUrl} target="_blank" rel="noopener noreferrer" aria-label="Open Gemini Enterprise in a new tab">
                  Open Gemini Enterprise <OpenInNewIcon size={13} />
                </a>
              ) : (
                <span className="tag tag-ge">GE Demo</span>
              )
            ) : (
              <label className={`llm-toggle ${d.useLlm ? "on" : ""}`} title="Send the natural-language prompt to Gemini (otherwise: deterministic probes)">
                <input type="checkbox" checked={d.useLlm} onChange={(e) => d.toggleLlm(e.target.checked)} />
                <SparkIcon size={14} /> Use Gemini
              </label>
            )}
          </div>
          {ge && (
            <div className="ge-hint" role="note">
              <CopyIcon size={14} />
              <div>
                <b>GE Demo:</b> clicking a test copies its prompt. Paste it into Gemini Enterprise and send it there. Policy toggles still apply here; use <i>Show what happened</i> to light up the connections that prompt uses.
                {isIngress && <div className="ge-hint-ingress">Ingress tests: Gemini Enterprise calls the agent through its own path, so it may not pass through the ingress gateway shown here.</div>}
                {!geUrl && <div className="ge-hint-ingress">No Gemini Enterprise app URL is configured (gemini_enterprise.app_url).</div>}
              </div>
            </div>
          )}
          <div className="tests">
            {sc.tests.map((t) => {
              const running = d.runningTest === t.id;
              const recording = d.busy === `record:${t.id}`;
              const showing = d.busy === `show:${t.id}`;
              return (
                <div key={t.id} className={`test-row ${ge ? "ge" : ""}`}>
                  <button
                    className={`test-btn ${running ? "running" : ""} ${t.malicious ? "malicious" : ""}`}
                    disabled={!ge && !!d.runningTest && !running}
                    onClick={() => (ge ? d.copyPrompt(t.id) : running ? d.stopTest() : d.runTest(t.id))}
                    title={ge ? `Copy prompt: ${t.prompt}` : t.prompt}
                    aria-label={ge ? `Copy the prompt for ${t.label}` : running ? `Stop ${t.label}` : `Run ${t.label}`}
                  >
                    <span className="test-icon">{ge ? <CopyIcon size={15} /> : running ? <span className="mini-spinner light" /> : <PlayIcon size={16} />}</span>
                    <span className="test-text">
                      <span className="test-label">
                        {t.label}
                        {t.malicious && <span className="tag tag-orange">malicious</span>}
                      </span>
                      <span className="test-prompt">“{t.prompt}”</span>
                    </span>
                    {running && !ge && <span className="test-stop">Stop</span>}
                    {ge && <span className="test-copy-hint">Copy</span>}
                  </button>
                  {ge && (
                    <button
                      className="show-btn"
                      disabled={!!d.busy || !!d.runningTest}
                      onClick={() => d.showWhatHappened(t.id)}
                      aria-label={`Show what happened for ${t.label}: probe the connections it uses`}
                      title="Probe the connections this prompt uses and show the result on the diagram"
                    >
                      {showing ? <span className="mini-spinner" /> : <EyeIcon size={13} />}
                      {showing ? "Probing…" : "Show what happened"}
                    </button>
                  )}
                  {showRecord && (
                    <button
                      className={`rec-btn ${recording ? "on" : ""}`}
                      disabled={!!d.runningTest || (!!d.busy && !recording)}
                      title="Run this test against Live GCP and save it as a recording (used by Demo and fallback replays)"
                      onClick={() => d.recordTest(t.id)}
                    >
                      {recording ? <span className="mini-spinner" /> : <span className="rec-dot" />}
                      {recording ? "Recording…" : "Record"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <button className="btn btn-text btn-sm probe-btn" disabled={d.busy === "probe" || !!d.runningTest} onClick={d.probe} title="Probe every edge and refresh the diagram">
            <RefreshIcon size={14} /> {d.busy === "probe" ? "Probing…" : "Probe all connections"}
          </button>
        </section>
      </div>

      <section className="card log-card">
        <div className="card-head">
          <h3>Activity</h3>
          <div className="log-tools" role="toolbar" aria-label="Activity actions">
            <button className="mini-btn" onClick={() => setGwOpen(true)} aria-label="Open the gateway logs from Cloud Logging" title="Agent Gateway decisions from Cloud Logging">
              <LogIcon size={13} /> Gateway logs
            </button>
            <button className="mini-btn" onClick={() => setLogExpanded(true)} aria-label="Expand the activity log" title="Expand">
              <ExpandIcon size={13} /> Expand
            </button>
            <CopyButton className="mini-btn" ariaLabel="Copy the activity log as plain text" getText={() => logToText(d.log)} />
            <button className="mini-btn" onClick={d.clearLog} disabled={!d.log.length} aria-label="Clear the activity log">
              Clear
            </button>
          </div>
        </div>
        <LogList log={d.log} onOpenLog={d.openLog} />
      </section>
      {logExpanded && (
        <GlassModal
          className="sheet-log"
          icon={<ClockIcon size={18} />}
          title="Activity"
          subtitle={
            <>
              {d.log.length} entr{d.log.length === 1 ? "y" : "ies"} · {theme.name} · {sc.title}
              {d.runningTest && <span className="live-dot">live</span>}
            </>
          }
          actions={<CopyButton ariaLabel="Copy the activity log as plain text" getText={() => logToText(d.log)} />}
          onClose={() => setLogExpanded(false)}
        >
          <LogList log={d.log} big onOpenLog={d.openLog} />
        </GlassModal>
      )}
      {gwOpen && <GatewayLogsSheet d={d} api={api} onClose={() => setGwOpen(false)} />}
    </aside>
  );
}
