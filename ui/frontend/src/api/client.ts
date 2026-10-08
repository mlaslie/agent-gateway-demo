// HTTP client for the FastAPI backend (CONTRACTS §7).
import type { AppConfig, GatewayLogsQuery, GatewayLogsResponse, Mode, PolicyStatus, ProbeResult, RecordResult, SseEvent, ThemeResponse, ThemeState, VerifyResult, SyncResult } from "./types";

export interface Api {
  getConfig(): Promise<AppConfig>;
  getTheme(id: string): Promise<ThemeResponse>;
  getState(id: string, mode: Mode): Promise<ThemeState>;
  setPolicy(id: string, pid: string, action: "apply" | "remove", mode: Mode): Promise<PolicyStatus>;
  setModelArmor(enabled: boolean, mode: Mode): Promise<{ enabled: boolean; status: string }>;
  runTest(
    id: string,
    testId: string,
    body: { mode: Mode; use_llm: boolean; scenario_id?: string },
    onEvent: (e: SseEvent) => void,
    signal?: AbortSignal,
  ): Promise<void>;
  probe(id: string, mode: Mode): Promise<ProbeResult>;
  record(id: string, testId: string, body: { use_llm: boolean; scenario_id?: string }): Promise<RecordResult>;
  reset(id: string, mode: Mode): Promise<unknown>;
  verify(id: string, mode: Mode): Promise<VerifyResult>;
  sync(id: string, mode: Mode): Promise<SyncResult>;
  explain(id: string, pid: string): Promise<{ lines: string[] }>;
  /** CONTRACTS §9: Agent Gateway request log entries for the theme, newest first. */
  gatewayLogs(id: string, mode: Mode, q?: GatewayLogsQuery): Promise<GatewayLogsResponse>;
  readonly isMock: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

/** Parse a text/event-stream body (fetch + ReadableStream; EventSource cannot POST). */
export async function readSse(body: ReadableStream<Uint8Array>, onEvent: (e: SseEvent) => void): Promise<void> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const flush = (chunk: string) => {
    const data = chunk
      .split(/\r?\n/)
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data) as SseEvent);
    } catch {
      onEvent({ type: "status", text: data });
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let m: RegExpExecArray | null;
    const sep = /\r?\n\r?\n/;
    while ((m = sep.exec(buf))) {
      flush(buf.slice(0, m.index));
      buf = buf.slice(m.index + m[0].length);
    }
  }
  if (buf.trim()) flush(buf);
}

const enc = encodeURIComponent;

export const httpApi: Api = {
  isMock: false,
  getConfig: () => req("GET", "/api/config"),
  getTheme: (id) => req("GET", `/api/themes/${enc(id)}`),
  getState: (id, mode) => req("GET", `/api/themes/${enc(id)}/state?mode=${enc(mode)}`),
  setPolicy: (id, pid, action, mode) => req("POST", `/api/themes/${enc(id)}/policies/${enc(pid)}`, { action, mode }),
  setModelArmor: (enabled, mode) => req("POST", "/api/model-armor", { enabled, mode }),
  probe: (id, mode) => req("POST", `/api/themes/${enc(id)}/probe`, { mode }),
  reset: (id, mode) => req("POST", `/api/themes/${enc(id)}/reset`, { mode }),
  verify: (id, mode) => req("POST", `/api/themes/${enc(id)}/verify`, { mode }),
  sync: (id, mode) => req("POST", `/api/themes/${enc(id)}/sync`, { mode }),
  record: (id, testId, body) => req("POST", `/api/themes/${enc(id)}/tests/${enc(testId)}/record`, body),
  explain: (id, pid) => req("GET", `/api/themes/${enc(id)}/policies/${enc(pid)}/explain`),
  gatewayLogs: (id, mode, q = {}) => {
    const qs = new URLSearchParams({ mode });
    if (q.since) qs.set("since", q.since);
    if (q.denied_only !== undefined) qs.set("denied_only", String(q.denied_only));
    if (q.limit !== undefined) qs.set("limit", String(q.limit));
    return req("GET", `/api/themes/${enc(id)}/gateway-logs?${qs}`);
  },
  async runTest(id, testId, body, onEvent, signal) {
    const r = await fetch(`/api/themes/${enc(id)}/tests/${enc(testId)}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
    if (!r.ok || !r.body) {
      let msg = `${r.status} ${r.statusText}`;
      try {
        const j = await r.json();
        msg = typeof j.detail === "string" ? j.detail : msg;
      } catch {
        /* ignore */
      }
      throw new ApiError(r.status, msg);
    }
    await readSse(r.body, onEvent);
  },
};
