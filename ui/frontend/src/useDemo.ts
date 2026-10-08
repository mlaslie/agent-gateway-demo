// Central app state: config, theme, polled state, optimistic policy toggles, SSE test runs, activity log.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Api } from "./api/client";
import type { AppConfig, EdgeState, Mode, PolicyStatus, Scenario, SseEvent, Theme, ThemeState } from "./api/types";
import { copyText } from "./util";

export interface LogEntry {
  id: number;
  ts: number;
  kind: "status" | "agent" | "tool" | "user" | "edge" | "fallback" | "error" | "system" | "done" | "ge";
  text: string;
  edge?: string;
  edgeState?: EdgeState;
  replayed?: boolean;
  testLabel?: string;
}

const LS = {
  get: (k: string) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* ignore */
    }
  },
};

let logSeq = Date.now();

export type ToastTone = "info" | "ok" | "error";
export interface Toast {
  id: number;
  msg: string;
  tone: ToastTone;
}

/** Key used for Model Armor in the "pending since" bookkeeping. */
export const MA_KEY = "__model_armor__";

export function useDemo(api: Api) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [themeId, setThemeId] = useState<string>("");
  const [mode, setModeRaw] = useState<Mode>("demo");
  const [theme, setTheme] = useState<Theme | null>(null);
  const [themeError, setThemeError] = useState<string | null>(null);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [serverState, setServerState] = useState<ThemeState | null>(null);
  const [overrides, setOverrides] = useState<Record<string, PolicyStatus>>({});
  const [maOverride, setMaOverride] = useState<boolean | null>(null);
  const [testEdges, setTestEdges] = useState<Record<string, EdgeState>>({});
  const [fallbackShown, setFallbackShown] = useState(false); // diagram currently shows a replay/simulated fallback
  const [inFlight, setInFlight] = useState<Set<string>>(new Set());
  const [runningTest, setRunningTest] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [useLlm, setUseLlm] = useState<boolean>(LS.get("agdemo.useLlm") === "1");
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [geDemo, setGeDemoRaw] = useState<boolean>(LS.get("agdemo.geDemo") === "1");
  // When the user clicked a policy / Model Armor toggle: fallback start time for the progress timer
  // until the server reports `changed_at`. A ref so the 5 s polling never resets it.
  const clickedAt = useRef<Map<string, number>>(new Map());
  const abortRef = useRef<AbortController | null>(null);
  const reqSeq = useRef(0);

  const addLog = useCallback((e: Omit<LogEntry, "id" | "ts">) => {
    const entry = { ...e, id: ++logSeq, ts: Date.now() };
    setLog((l) => [...l.slice(-300), entry]);
  }, []);

  const flash = useCallback((msg: string, tone: ToastTone = "info") => {
    const id = ++logSeq;
    setToast({ id, msg, tone });
    setTimeout(() => setToast((t) => (t?.id === id ? null : t)), 4500);
  }, []);

  // ---------- config ----------
  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setConfig(c);
        const qsTheme = new URLSearchParams(location.search).get("theme");
        const tid = (qsTheme && c.themes.some((t) => t.id === qsTheme) && qsTheme) || LS.get("agdemo.theme") || c.default_theme || c.themes[0]?.id;
        setThemeId(c.themes.some((t) => t.id === tid) ? tid : c.default_theme);
        const savedMode = LS.get("agdemo.mode") as Mode | null;
        let m: Mode = savedMode && ["live", "demo", "live_with_fallback"].includes(savedMode) ? savedMode : c.default_mode;
        if (!c.live_available) m = "demo";
        const chosen = c.themes.find((t) => t.id === tid);
        if (chosen && !chosen.deployed && m !== "demo") m = "demo"; // undeployed theme: Live can't work
        setModeRaw(m);
      })
      .catch((e) => setFatal(`Could not reach the demo backend: ${e.message ?? e}`));
  }, [api]);

  const themeSummary = config?.themes.find((t) => t.id === themeId);

  // ---------- theme ----------
  useEffect(() => {
    if (!themeId) return;
    let cancel = false;
    setTheme(null);
    setThemeError(null);
    setServerState(null);
    setOverrides({});
    setTestEdges({});
    api
      .getTheme(themeId)
      .then((r) => {
        if (cancel) return;
        setTheme(r.theme);
        const saved = LS.get(`agdemo.scenario.${themeId}`);
        const sc = r.theme.scenarios.find((s) => s.id === saved) ?? r.theme.scenarios[0];
        setScenarioId(sc?.id ?? "");
      })
      .catch((e) => !cancel && setThemeError(e.message ?? String(e)));
    LS.set("agdemo.theme", themeId);
    return () => {
      cancel = true;
    };
  }, [api, themeId]);

  const scenario: Scenario | null = useMemo(() => theme?.scenarios.find((s) => s.id === scenarioId) ?? theme?.scenarios[0] ?? null, [theme, scenarioId]);

  // ---------- state polling ----------
  const refresh = useCallback(async () => {
    if (!themeId || !theme || theme.id !== themeId) return;
    const seq = ++reqSeq.current;
    try {
      const s = await api.getState(themeId, mode);
      if (seq !== reqSeq.current) return;
      setServerState(s);
    } catch (e) {
      if (seq === reqSeq.current) flash(`State refresh failed: ${(e as Error).message}`, "error");
    }
  }, [api, themeId, theme, mode, flash]);

  useEffect(() => {
    refresh();
    const live = mode !== "demo";
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, live ? 5000 : 10000);
    return () => clearInterval(iv);
  }, [refresh, mode]);

  // Merge optimistic overrides into server state.
  const state: ThemeState | null = useMemo(() => {
    if (!serverState) return null;
    const policies = { ...serverState.policies, ...overrides };
    let gateways = serverState.gateways;
    if (theme) {
      const gwFor = (path: string) => theme.policies.find((p) => p.type === "gateway_attach" && p.params.path === path)?.id;
      const g = { ...gateways };
      for (const path of ["egress", "ingress"] as const) {
        const pid = gwFor(path);
        if (pid && overrides[pid]) g[path] = { attached: overrides[pid].applied, status: overrides[pid].status };
      }
      gateways = g;
    }
    const model_armor = maOverride !== null ? { ...serverState.model_armor, enabled: maOverride, status: "pending" } : serverState.model_armor;
    return { ...serverState, policies, gateways, model_armor };
  }, [serverState, overrides, maOverride, theme]);

  // Clear the test-result overlay whenever the effective policy set changes.
  const signature = useMemo(() => {
    if (!state) return "";
    const ap = Object.entries(state.policies)
      .filter(([, v]) => v.applied)
      .map(([k]) => k)
      .sort()
      .join("+");
    return `${ap}|${state.model_armor.enabled}`;
  }, [state]);
  useEffect(() => {
    setTestEdges({});
    setFallbackShown(false);
  }, [signature, mode, themeId]);

  const edgeStates: Record<string, EdgeState> = useMemo(() => ({ ...(state?.edges ?? {}), ...testEdges }), [state, testEdges]);

  // ---------- actions ----------
  const canAdmin = !!config?.can_admin;

  const setPolicy = useCallback(
    async (pid: string, action: "apply" | "remove", quiet = false) => {
      if (!themeId || !theme) return;
      const p = theme.policies.find((x) => x.id === pid);
      clickedAt.current.set(pid, Date.now());
      setOverrides((o) => ({
        ...o,
        [pid]: { applied: action === "apply", status: action === "apply" ? "pending" : "pending_removal", detail: "Sending…" },
      }));
      if (!quiet) addLog({ kind: "system", text: `${action === "apply" ? "Applying" : "Removing"} policy: ${p?.text ?? pid}` });
      try {
        const ps = await api.setPolicy(themeId, pid, action, mode);
        setOverrides((o) => ({ ...o, [pid]: ps }));
        if (ps.status === "error") addLog({ kind: "error", text: `Policy ${pid}: ${ps.detail || "error"}` });
        await refresh();
      } catch (e) {
        addLog({ kind: "error", text: `Policy ${pid} failed: ${(e as Error).message}` });
        flash(`Could not ${action} policy: ${(e as Error).message}`, "error");
        await refresh();
      } finally {
        setOverrides((o) => {
          const n = { ...o };
          delete n[pid];
          return n;
        });
      }
    },
    [api, themeId, theme, mode, addLog, refresh, flash],
  );

  const applyPreconditions = useCallback(
    async (quiet = false) => {
      if (!scenario || !state) return;
      const missing = scenario.preconditions.filter((pid) => !state.policies[pid]?.applied);
      if (!missing.length) return;
      if (!quiet) addLog({ kind: "system", text: `Applying scenario preconditions: ${missing.join(", ")}` });
      await Promise.all(missing.map((pid) => setPolicy(pid, "apply", true)));
    },
    [scenario, state, setPolicy, addLog],
  );

  // Demo mode: preconditions are applied automatically on entering a tab (Live: offered, not forced).
  const autoApplied = useRef<string>("");
  useEffect(() => {
    if (!scenario || !state || mode !== "demo" || !canAdmin) return;
    const key = `${themeId}|${scenario.id}|${mode}`;
    if (autoApplied.current === key) return;
    autoApplied.current = key;
    const missing = scenario.preconditions.filter((pid) => !state.policies[pid]?.applied);
    if (missing.length) {
      addLog({ kind: "system", text: `Scenario "${scenario.title}": auto-applying preconditions (${missing.join(", ")})` });
      applyPreconditions(true);
    }
  }, [scenario, state, mode, canAdmin, themeId, applyPreconditions, addLog]);

  const setModelArmor = useCallback(
    async (enabled: boolean) => {
      setMaOverride(enabled);
      clickedAt.current.set(MA_KEY, Date.now());
      addLog({ kind: "system", text: `Model Armor ${enabled ? "enabled" : "disabled"} on the gateways` });
      try {
        await api.setModelArmor(enabled, mode);
        await refresh();
      } catch (e) {
        addLog({ kind: "error", text: `Model Armor: ${(e as Error).message}` });
        flash(`Model Armor change failed: ${(e as Error).message}`, "error");
        await refresh();
      } finally {
        setMaOverride(null);
      }
    },
    [api, mode, addLog, refresh, flash],
  );

  // Full "back to step 1": remove every policy for this theme, detach the gateways, turn Model Armor
  // off (it is global), jump to the first scenario tab and start a fresh activity log.
  const reset = useCallback(async () => {
    if (!themeId) return;
    abortRef.current?.abort();
    setBusy("reset");
    setLog([]);
    setTestEdges({});
    const first = theme?.scenarios[0]?.id;
    if (first) {
      setScenarioId(first);
      LS.set(`agdemo.scenario.${themeId}`, first);
    }
    const ma = serverState?.model_armor;
    const maOn = !!ma && (ma.enabled || ma.status === "pending");
    addLog({
      kind: "system",
      text: `Resetting to the start: removing all policies, detaching the gateways${maOn ? ", turning Model Armor off" : ""}…`,
    });
    try {
      if (maOn) clickedAt.current.set(MA_KEY, Date.now());
      await Promise.all([api.reset(themeId, mode), maOn ? api.setModelArmor(false, mode) : Promise.resolve()]);
      await refresh();
      addLog({
        kind: "system",
        text: mode === "demo" ? "Reset complete." : "Reset requested. GCP changes show as pending until they finish.",
      });
    } catch (e) {
      addLog({ kind: "error", text: `Reset failed: ${(e as Error).message}` });
      flash(`Reset failed: ${(e as Error).message}`, "error");
      await refresh();
    } finally {
      setBusy(null);
    }
  }, [api, themeId, theme, serverState, mode, addLog, refresh, flash]);

  const probe = useCallback(async () => {
    if (!themeId) return;
    setBusy("probe");
    addLog({ kind: "status", text: "Probing every connection…" });
    try {
      const r = await api.probe(themeId, mode);
      setFallbackShown(!!r.fallback);
      if (r.fallback) {
        addLog({ kind: "fallback", text: `Live probe failed (${r.fallback}) — showing simulated edges`, replayed: true });
        const marked: Record<string, EdgeState> = {};
        for (const [k, v] of Object.entries(r.edges ?? {})) marked[k] = { ...v, source: v.source ?? "replayed" };
        setTestEdges(marked);
      } else {
        setTestEdges(r.edges ?? {});
      }
      await refresh();
      addLog({ kind: "done", text: r.fallback ? "Probe finished (fallback)." : "Probe complete — diagram shows the observed state.", replayed: !!r.fallback });
    } catch (e) {
      addLog({ kind: "error", text: `Probe failed: ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  }, [api, themeId, mode, addLog, refresh]);

  const runTest = useCallback(
    async (testId: string) => {
      if (!themeId || !scenario) return;
      const test = scenario.tests.find((t) => t.id === testId);
      if (!test) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setRunningTest(testId);
      const probes = new Set(test.probes.map((p) => p.edge));
      setInFlight(new Set(probes));
      setTestEdges((te) => {
        const n = { ...te };
        for (const e of probes) delete n[e];
        return n;
      });
      let replayed = false;
      setFallbackShown(false);
      addLog({ kind: "system", text: `▶ ${test.label}`, testLabel: test.label });
      if (!test.caller) addLog({ kind: "user", text: test.prompt });
      const onEvent = (ev: SseEvent) => {
        switch (ev.type) {
          case "status":
            addLog({ kind: "status", text: ev.text, replayed });
            break;
          case "message":
            if (ev.role === "user" && !test.caller) break; // already logged
            addLog({ kind: ev.role === "agent" ? "agent" : ev.role === "user" ? "user" : "tool", text: ev.text, replayed });
            break;
          case "fallback":
            replayed = true;
            setFallbackShown(true);
            addLog({ kind: "fallback", text: ev.reason, replayed: true });
            break;
          case "edge":
            setTestEdges((te) => ({ ...te, [ev.edge]: ev.state }));
            setInFlight((s) => {
              const n = new Set(s);
              n.delete(ev.edge);
              return n;
            });
            addLog({ kind: "edge", text: ev.state.detail ?? "", edge: ev.edge, edgeState: ev.state, replayed: replayed || ev.state.source === "replayed" });
            break;
          case "done":
            setTestEdges((te) => ({ ...te, ...ev.edges }));
            setInFlight(new Set());
            addLog({ kind: "done", text: "Test finished.", replayed });
            break;
          case "error":
            addLog({ kind: "error", text: ev.text, replayed });
            break;
        }
      };
      try {
        await api.runTest(themeId, testId, { mode, use_llm: useLlm, scenario_id: scenario.id }, onEvent, ac.signal);
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          addLog({ kind: "error", text: `Test failed: ${(e as Error).message}` });
        }
      } finally {
        if (abortRef.current === ac) {
          setRunningTest(null);
          setInFlight(new Set());
        }
      }
    },
    [api, themeId, scenario, mode, useLlm, addLog],
  );

  const recordTest = useCallback(
    async (testId: string) => {
      if (!themeId || !scenario) return;
      const test = scenario.tests.find((t) => t.id === testId);
      setBusy(`record:${testId}`);
      addLog({ kind: "system", text: `● Recording "${test?.label ?? testId}" against Live GCP…` });
      try {
        const r = await api.record(themeId, testId, { use_llm: useLlm, scenario_id: scenario.id });
        addLog({ kind: "done", text: `Recorded ${r.events} events · signature ${r.signature} · saved to ${r.saved}` });
      } catch (e) {
        addLog({ kind: "error", text: `Recording failed: ${(e as Error).message}` });
        flash(`Recording failed: ${(e as Error).message}`, "error");
      } finally {
        setBusy(null);
      }
    },
    [api, themeId, scenario, useLlm, addLog, flash],
  );

  // ---------- GE Demo: prompts are sent from Gemini Enterprise, this UI copies them ----------
  const setGeDemo = useCallback(
    (v: boolean) => {
      setGeDemoRaw(v);
      LS.set("agdemo.geDemo", v ? "1" : "0");
      addLog({ kind: "ge", text: v ? "GE Demo on: clicking a test copies its prompt for Gemini Enterprise." : "GE Demo off: tests run from this UI." });
    },
    [addLog],
  );

  const copyPrompt = useCallback(
    async (testId: string) => {
      const test = scenario?.tests.find((t) => t.id === testId);
      if (!test) return;
      let ok = false;
      try {
        ok = await copyText(test.prompt);
      } catch {
        ok = false;
      }
      addLog({ kind: "ge", text: `${ok ? "Copied" : "Copy this"} prompt for “${test.label}”: ${test.prompt}`, testLabel: test.label });
      if (ok) flash("Prompt copied: paste it into Gemini Enterprise", "ok");
      else flash("Couldn't copy automatically. The prompt is in the Activity log; copy it from there.", "error");
    },
    [scenario, addLog, flash],
  );

  /** GE Demo: probe the connections a test would use so the diagram shows the current policy for them. */
  const showWhatHappened = useCallback(
    async (testId: string) => {
      if (!themeId || !scenario) return;
      const test = scenario.tests.find((t) => t.id === testId);
      if (!test) return;
      const edges = test.probes.map((p) => p.edge);
      setBusy(`show:${testId}`);
      setInFlight(new Set(edges));
      addLog({ kind: "status", text: `Probing the connections “${test.label}” uses (${edges.join(", ")})…`, testLabel: test.label });
      try {
        const r = await api.probe(themeId, mode);
        const picked: Record<string, EdgeState> = {};
        for (const e of edges) {
          const st = r.edges?.[e];
          if (!st) continue;
          picked[e] = r.fallback ? { ...st, source: st.source ?? "replayed" } : st;
        }
        setFallbackShown(!!r.fallback);
        setTestEdges((te) => ({ ...te, ...picked }));
        if (r.fallback) addLog({ kind: "fallback", text: `Live probe failed (${r.fallback}): showing simulated edges`, replayed: true });
        for (const [e, st] of Object.entries(picked))
          addLog({ kind: "edge", text: st.detail ?? "", edge: e, edgeState: st, replayed: !!r.fallback || st.source === "replayed" });
        await refresh();
      } catch (e) {
        addLog({ kind: "error", text: `Probe failed: ${(e as Error).message}` });
        flash(`Probe failed: ${(e as Error).message}`, "error");
      } finally {
        setInFlight(new Set());
        setBusy(null);
      }
    },
    [api, themeId, scenario, mode, addLog, flash, refresh],
  );

  /** Start time (ms) of a pending change: server `changed_at`, else when the user clicked. */
  const pendingSince = useCallback((key: string, changedAt?: string | null): number | null => {
    const t = changedAt ? Date.parse(changedAt) : NaN;
    if (!Number.isNaN(t)) return t;
    return clickedAt.current.get(key) ?? null;
  }, []);

  const stopTest = useCallback(() => {
    abortRef.current?.abort();
    setRunningTest(null);
    setInFlight(new Set());
    addLog({ kind: "system", text: "Test cancelled." });
  }, [addLog]);

  const setMode = useCallback(
    (m: Mode) => {
      if (m !== "demo" && config && !config.live_available) return;
      abortRef.current?.abort();
      setModeRaw(m);
      LS.set("agdemo.mode", m);
      setServerState(null);
      autoApplied.current = "";
      addLog({ kind: "system", text: `Mode: ${m === "demo" ? "Demo (simulated)" : m === "live" ? "Live" : "Live with fallback"}` });
    },
    [config, addLog],
  );

  const selectTheme = useCallback(
    (id: string) => {
      abortRef.current?.abort();
      setThemeId(id);
      const t = config?.themes.find((x) => x.id === id);
      addLog({ kind: "system", text: `Theme: ${t?.name ?? id}` });
      if (t && !t.deployed && mode !== "demo") {
        setModeRaw("demo");
        flash(`${t.name} is not deployed — switched to Demo (simulated) mode.`);
      }
    },
    [config, mode, flash, addLog],
  );

  const selectScenario = useCallback(
    (id: string) => {
      setScenarioId(id);
      LS.set(`agdemo.scenario.${themeId}`, id);
    },
    [themeId],
  );

  const toggleLlm = useCallback((v: boolean) => {
    setUseLlm(v);
    LS.set("agdemo.useLlm", v ? "1" : "0");
  }, []);

  return {
    config,
    isMock: api.isMock,
    fatal,
    themeId,
    themeSummary,
    selectTheme,
    mode,
    setMode,
    theme,
    themeError,
    scenario,
    selectScenario,
    state,
    edgeStates,
    inFlight,
    runningTest,
    runTest,
    fallbackShown,
    recordTest,
    stopTest,
    log,
    clearLog: () => setLog([]),
    useLlm,
    toggleLlm,
    setPolicy,
    applyPreconditions,
    setModelArmor,
    reset,
    probe,
    busy,
    canAdmin,
    toast,
    flash,
    dismissToast: () => setToast(null),
    geDemo,
    setGeDemo,
    copyPrompt,
    showWhatHappened,
    pendingSince,
    refresh,
  };
}

export type Demo = ReturnType<typeof useDemo>;
