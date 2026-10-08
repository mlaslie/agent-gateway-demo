// Small shared helpers: clipboard with a fallback, duration formatting, activity-log plain text.
import type { LogEntry } from "./useDemo";

/** Copy text to the clipboard. Tries the async Clipboard API, then a hidden-textarea execCommand fallback. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* blocked (permissions, automation, unfocused document): fall back below */
  }
  const active = document.activeElement as HTMLElement | null;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.width = "1px";
  ta.style.height = "1px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta.remove();
    active?.focus?.({ preventScroll: true });
  }
}

/** 330 → "5 min 30 s", 90 → "1 min 30 s", 60 → "1 min", 45 → "45 s". */
export function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (!m) return `${r} s`;
  return r ? `${m} min ${r} s` : `${m} min`;
}

/** Elapsed seconds → "mm:ss" (or "h:mm:ss" past an hour). */
export function fmtClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

export const fmtTime = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const LOG_TAG: Record<LogEntry["kind"], string> = {
  status: "STATUS",
  agent: "AGENT",
  tool: "TOOL",
  user: "PROMPT",
  edge: "EDGE",
  fallback: "FALLBACK",
  error: "ERROR",
  system: "SYSTEM",
  done: "DONE",
  ge: "GE",
  gwlog: "GATEWAY LOG",
};

/** One plain-text line per log entry: time, tag, text, edge state. */
export function logToText(log: LogEntry[]): string {
  return log
    .map((e) => {
      const parts = [fmtTime(e.ts), `[${LOG_TAG[e.kind] ?? e.kind}]`];
      if (e.kind === "edge" && e.edge) {
        const st = e.edgeState;
        parts.push(`${e.edge} → ${st?.state ?? "?"}${st?.http_status ? ` (${st.http_status})` : ""}${st?.source ? ` [${st.source}]` : ""}`);
        if (e.text) parts.push(`— ${e.text}`);
      } else {
        parts.push(e.text);
      }
      if (e.kind === "gwlog" && e.gw) parts.push(e.gw.simulated ? "(simulated)" : e.gw.console_url ? `<${e.gw.console_url}>` : "");
      if (e.replayed && e.kind !== "edge") parts.push("(replayed)");
      return parts.join(" ");
    })
    .join("\n");
}
