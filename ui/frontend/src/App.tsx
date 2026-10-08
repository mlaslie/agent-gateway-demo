import type { Api } from "./api/client";
import { Diagram } from "./diagram/Diagram";
import { Header } from "./components/Header";
import { SidePanel } from "./components/SidePanel";
import { TestResults } from "./components/TestResults";
import { GatewayLogViewer } from "./components/GatewayLogs";
import { CheckIcon, CloseIcon, WarnIcon } from "./components/Icons";
import { useDemo } from "./useDemo";

export default function App({ api }: { api: Api }) {
  const d = useDemo(api);

  if (d.fatal) {
    return (
      <div className="fatal">
        <WarnIcon size={32} />
        <h1>Backend unavailable</h1>
        <p>{d.fatal}</p>
        <p className="muted">
          Start the backend on :8080, or open <a href="?mock=1">?mock=1</a> to run against the in-browser mock.
        </p>
      </div>
    );
  }

  const notices: { tone: "info" | "warn"; text: string }[] = [];
  if (d.config && !d.config.live_available)
    notices.push({
      tone: "info",
      text: `Live GCP access isn't available${d.config.live_unavailable_reason ? ` (${d.config.live_unavailable_reason})` : ""} — running in Demo (simulated) mode.`,
    });
  if (d.state?.live_error && d.mode !== "demo") notices.push({ tone: "warn", text: `Live state unavailable: ${d.state.live_error}` });
  if (d.config && !d.config.can_admin) notices.push({ tone: "info", text: "View-only: policy toggles, Model Armor and Reset are disabled for your account." });
  if (d.themeSummary && !d.themeSummary.deployed && d.mode !== "demo") notices.push({ tone: "warn", text: `${d.themeSummary.name} isn't deployed in this project — Live calls will fail. Use Demo mode.` });

  const sc = d.scenario;
  return (
    <div className="app">
      <Header d={d} />
      {notices.length > 0 && (
        <div className={`notice notice-${notices.some((n) => n.tone === "warn") ? "warn" : "info"}`}>{notices.map((n) => n.text).join("  ·  ")}</div>
      )}
      {d.theme && (
        <nav className="tabs" role="tablist">
          {d.theme.scenarios.map((s) => (
            <button key={s.id} role="tab" aria-selected={sc?.id === s.id} className={`tab ${sc?.id === s.id ? "on" : ""}`} onClick={() => d.selectScenario(s.id)}>
              <span className="tab-num">{s.order}</span>
              <span className="tab-text">
                <span className="tab-title">{s.title}</span>
                {s.subtitle && <span className="tab-sub">{s.subtitle}</span>}
              </span>
            </button>
          ))}
        </nav>
      )}
      <main className="main">
        <section className="stage">
          {d.themeError && (
            <div className="stage-msg">
              <WarnIcon size={28} />
              <div>Couldn't load theme: {d.themeError}</div>
            </div>
          )}
          {!d.themeError && (!d.theme || !sc) && <div className="stage-msg">Loading…</div>}
          {d.theme && sc && <Diagram theme={d.theme} scenario={sc} state={d.state} edgeStates={d.edgeStates} inFlight={d.inFlight} edgeLogs={d.edgeLogs} onOpenLog={d.openLog} />}
          {d.resultsOpen && d.testRun && <TestResults d={d} />}
          {d.state && <ModeBadge mode={d.mode} mock={d.isMock} replayed={d.fallbackShown || Object.values(d.edgeStates).some((e) => e.source === "replayed")} />}
        </section>
        <SidePanel d={d} api={api} />
      </main>
      {d.viewLog && <GatewayLogViewer key={d.viewLog.id} entry={d.viewLog} onClose={d.closeLog} />}
      {d.toast && (
        <div key={d.toast.id} className={`glass toast toast-${d.toast.tone}`} role={d.toast.tone === "error" ? "alert" : "status"}>
          {d.toast.tone === "ok" && (
            <span className="toast-icon">
              <CheckIcon size={15} />
            </span>
          )}
          {d.toast.tone === "error" && (
            <span className="toast-icon">
              <WarnIcon size={15} />
            </span>
          )}
          <span className="toast-msg">{d.toast.msg}</span>
          <button className="icon-btn" onClick={d.dismissToast} aria-label="Dismiss notification">
            <CloseIcon size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

function ModeBadge({ mode, mock, replayed }: { mode: string; mock: boolean; replayed: boolean }) {
  const label = mode === "demo" ? "Simulated" : mode === "live" ? "Live GCP" : "Live · fallback";
  return (
    <div className="mode-badges">
      <div className={`mode-badge mb-${mode}`}>{label}</div>
      {replayed && (
        <div className="mode-badge mb-replayed" title="Some edges show a replayed recording (live result not available yet)">
          Replayed
        </div>
      )}
      {mock && (
        <div className="mode-badge mb-mock" title="Running against the in-browser mock backend (VITE_MOCK=1 / ?mock=1)">
          Mock backend
        </div>
      )}
    </div>
  );
}
