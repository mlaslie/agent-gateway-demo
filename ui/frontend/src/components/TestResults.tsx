// Floating, non-blocking "Test results" panel over the diagram: only the activity of the latest test run,
// including the gateway-log entries that run's Cloud Logging watch adds later.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { Demo, LogEntry, TestRun, TestRunStatus } from "../useDemo";
import { logToText } from "../util";
import { CopyButton, GlassModal } from "./Glass";
import { CloseIcon, ExpandIcon, PlayIcon } from "./Icons";
import { LogList } from "./LogList";

const CLOSE_MS = 180;

const STATUS: Record<TestRunStatus, string> = {
  running: "Running…",
  finished: "Finished",
  failed: "Failed",
  fallback: "Fallback-replayed",
  cancelled: "Cancelled",
};

const MODE: Record<TestRun["mode"], string> = {
  live: "Live",
  demo: "Demo",
  live_with_fallback: "Live + fallback",
};

const OUTCOME_ORDER = ["allowed", "denied", "blocked", "direct", "pending", "error", "unknown"];

const rank = (k: string) => {
  const i = OUTCOME_ORDER.indexOf(k);
  return i < 0 ? 99 : i;
};

/** "2 allowed · 3 denied · 1 blocked" from the run's final edge results (+ gateway log entries seen). */
export function runSummary(run: TestRun, entries: LogEntry[]): string {
  const counts = new Map<string, number>();
  for (const st of Object.values(run.results)) {
    if (!st) continue;
    counts.set(st.state, (counts.get(st.state) ?? 0) + 1);
  }
  const parts = [...counts.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]))
    .map(([k, n]) => `${n} ${k}`);
  const gw = entries.filter((e) => e.kind === "gwlog").length;
  if (gw) parts.push(`${gw} gateway log entr${gw === 1 ? "y" : "ies"}`);
  return parts.join(" · ");
}

function StatusPill({ status }: { status: TestRunStatus }) {
  return (
    <span className={`pill tr-status tr-${status}`}>
      {status === "running" && <span className="mini-spinner" aria-hidden="true" />}
      {STATUS[status]}
    </span>
  );
}

function ModeTag({ mode }: { mode: TestRun["mode"] }) {
  return <span className={`mode-badge tr-mode mb-${mode}`}>{MODE[mode]}</span>;
}

function Footer({ run, entries, watching }: { run: TestRun; entries: LogEntry[]; watching: boolean }) {
  const summary = run.status === "running" ? "" : runSummary(run, entries);
  if (!summary && !watching && run.status !== "running") return null;
  return (
    <div className="tr-foot" aria-live="polite">
      {summary && <span className="tr-summary">{summary}</span>}
      {watching && (
        <span className="tr-watch">
          <span className="mini-spinner" aria-hidden="true" /> Watching Cloud Logging…
        </span>
      )}
      {!summary && !watching && run.status === "running" && <span className="tr-watch">Streaming…</span>}
    </div>
  );
}

let lastFocusKey = 0;

export function TestResults({ d }: { d: Demo }) {
  const run = d.testRun;
  const entries = d.runLog;
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [closing, setClosing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const closeRef = useRef(d.closeResults);
  closeRef.current = d.closeResults;

  const closingRef = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const requestClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
      const el = panel.current;
      const back = returnFocus.current;
      if (el && el.contains(document.activeElement) && back && document.contains(back)) back.focus({ preventScroll: true });
      closeRef.current();
    }, reduce ? 0 : CLOSE_MS);
  }, []);

  // Take focus only when the user clicked a test (never while events stream in).
  useEffect(() => {
    if (d.resultsFocus > lastFocusKey) {
      lastFocusKey = d.resultsFocus;
      const prev = document.activeElement as HTMLElement | null;
      if (prev && !panel.current?.contains(prev)) returnFocus.current = prev;
      panel.current?.focus({ preventScroll: true });
    }
  }, [d.resultsFocus]);

  if (!run) return null;
  const watching = d.watchingRun === run.id;
  const copy = () => logToText(entries);
  const label = run.kind === "record" ? `● Record: ${run.label}` : run.label;

  return (
    <>
      <div
        ref={panel}
        className={`glass test-results ${closing ? "closing" : ""}`}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            requestClose();
          }
        }}
      >
        <div className="tr-head">
          <span className="tr-icon" aria-hidden="true">
            <PlayIcon size={15} />
          </span>
          <div className="tr-meta">
            <span className="tr-eyebrow">Test results</span>
            <StatusPill status={run.status} />
            <ModeTag mode={run.mode} />
          </div>
          <div className="tr-actions" role="toolbar" aria-label="Test results actions">
            <button className="mini-btn" onClick={() => setExpanded(true)} aria-label="Expand the test results" title="Expand">
              <ExpandIcon size={13} /> Expand
            </button>
            <CopyButton className="mini-btn" ariaLabel="Copy this test's results as plain text" getText={copy} />
            <button className="mini-btn" onClick={requestClose} aria-label="Close test results" title="Close (Esc)">
              <CloseIcon size={13} /> Close
            </button>
          </div>
        </div>
        <h2 id={titleId} className="tr-title" title={label}>
          {label}
        </h2>
        {run.prompt && <div className="tr-prompt">“{run.prompt}”</div>}
        <LogList log={entries} live={false} label="Test results log" className="tr-log" empty="Waiting for the first event…" onOpenLog={d.openLog} />
        <Footer run={run} entries={entries} watching={watching} />
      </div>
      {expanded && (
        <GlassModal
          className="sheet-log"
          icon={<PlayIcon size={18} />}
          title={`Test results · ${label}`}
          subtitle={
            <>
              <StatusPill status={run.status} />
              <ModeTag mode={run.mode} />
              {run.prompt && <span className="tr-sheet-prompt">“{run.prompt}”</span>}
            </>
          }
          actions={<CopyButton ariaLabel="Copy this test's results as plain text" getText={copy} />}
          onClose={() => setExpanded(false)}
        >
          <LogList log={entries} big label="Test results" empty="Waiting for the first event…" onOpenLog={d.openLog} />
          <Footer run={run} entries={entries} watching={watching} />
        </GlassModal>
      )}
    </>
  );
}
