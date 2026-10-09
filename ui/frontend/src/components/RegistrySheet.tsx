// Agent Registry browser (CONTRACTS §10): the theme's agents, MCP servers and platform endpoints as registered
// in Agent Registry, and who holds roles/iap.egressor on each (who may call it through Agent Gateway).
import { useCallback, useEffect, useRef, useState } from "react";
import type { Api } from "../api/client";
import type { RegistryAccess, RegistryItem, RegistryTool, RegistryView } from "../api/types";
import type { Demo } from "../useDemo";
import { fmtTime } from "../util";
import { GlassModal } from "./Glass";
import { BookIcon, CheckIcon, ChevronIcon, CloseIcon, OpenInNewIcon, PencilIcon, RefreshIcon, RegistryIcon, RobotIcon, ServerIcon } from "./Icons";

const REFRESH_MS = 10_000;
type Tab = "agents" | "mcp" | "endpoints";

const KIND_LABEL: Record<RegistryItem["kind"], string> = {
  a2a_agent: "A2A agent",
  orchestrator: "Agent Runtime",
  mcp_server: "MCP server",
  endpoint: "Endpoint",
};

function hintTitle(t: RegistryTool): string {
  const a = t.annotations;
  const keys = Object.keys(a) as (keyof typeof a)[];
  return keys.length ? `MCP annotations: ${keys.map((k) => `${k}: ${String(a[k])}`).join(", ")}` : "No MCP annotations";
}

function AccessRow({ a }: { a: RegistryAccess }) {
  return (
    <li className={`reg-acc reg-acc-${a.member_kind}`}>
      <span className="reg-acc-ok" aria-hidden="true">
        <CheckIcon size={13} />
      </span>
      <div className="reg-acc-main">
        <div className="reg-acc-who">
          <span className="reg-acc-name" title={a.member}>
            {a.member_label}
          </span>
          <span className={`reg-cond-tag ${a.condition ? "cond" : "uncond"}`}>{a.condition ? "conditional" : "unconditional"}</span>
          {a.policy_id && (
            <span className="reg-policy mono" title={a.policy_text ?? undefined}>
              {a.policy_id}
            </span>
          )}
          {a.note && <span className="reg-note">{a.note}</span>}
        </div>
        {a.condition && (
          <div className="reg-cond">
            {a.condition.title && <div className="reg-cond-title">{a.condition.title}</div>}
            <code className="mono reg-expr">{a.condition.expression}</code>
          </div>
        )}
      </div>
    </li>
  );
}

function Access({ item }: { item: RegistryItem }) {
  return (
    <div className="reg-access">
      <div className="reg-sec-title">Access · roles/iap.egressor</div>
      {item.access_error ? (
        <div className="reg-access-err">Couldn't read the IAP policy: {item.access_error}</div>
      ) : item.access === null ? (
        <div className="muted reg-small">Not available</div>
      ) : item.access.length === 0 ? (
        <div className="reg-deny">
          <CloseIcon size={13} /> No one — default deny
        </div>
      ) : (
        <ul className="reg-acc-list">
          {item.access.map((a, i) => (
            <AccessRow key={`${a.member}-${i}`} a={a} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ItemCard({ item }: { item: RegistryItem }) {
  const icon = item.kind === "mcp_server" ? <ServerIcon size={18} /> : item.kind === "endpoint" ? <RegistryIcon size={18} /> : <RobotIcon size={18} />;
  const skills = item.card?.skills?.length ? item.card.skills : (item.skills ?? []);
  return (
    <article className={`reg-card reg-${item.kind}`} aria-label={`${item.display_name} (${KIND_LABEL[item.kind]})`}>
      <header className="reg-card-head">
        <span className={`reg-icon reg-icon-${item.kind}`}>{icon}</span>
        <div className="reg-card-titles">
          <div className="reg-card-name">
            {item.display_name}
            <span className={`reg-kind reg-kind-${item.kind}`}>{KIND_LABEL[item.kind]}</span>
            {item.label && <span className="reg-note">{item.label}</span>}
          </div>
          {item.url && <div className="mono reg-url">{item.url}</div>}
          {item.resource && (
            <div className="mono reg-res" title="Agent Registry resource">
              {item.resource}
            </div>
          )}
        </div>
      </header>
      {item.description && <p className="reg-desc">{item.description}</p>}
      {item.error && <div className="reg-item-err">{item.error}</div>}
      {skills.length > 0 && (
        <div className="reg-skills">
          <div className="reg-sec-title">Skills{item.card?.protocol_version ? ` · A2A ${item.card.protocol_version}` : ""}</div>
          <div className="reg-chips">
            {skills.map((s) => (
              <span key={s.id} className="reg-chip" title={[s.description, s.tags.length ? `tags: ${s.tags.join(", ")}` : ""].filter(Boolean).join("\n")}>
                {s.name}
                <span className="mono reg-chip-id">{s.id}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      {item.tools && item.tools.length > 0 && (
        <div className="reg-tools">
          <div className="reg-sec-title">Tools (registered tool spec)</div>
          {item.tools.map((t) => (
            <div key={t.name} className="reg-tool" title={hintTitle(t)}>
              <span className={`reg-rw ${t.read_only ? "ro" : "rw"}`}>
                {t.read_only ? <BookIcon size={12} /> : <PencilIcon size={12} />}
                {t.read_only ? "READ" : "WRITE"}
              </span>
              <span className="mono reg-tool-name">{t.name}</span>
              <span className="reg-tool-desc">{t.description}</span>
            </div>
          ))}
        </div>
      )}
      <Access item={item} />
    </article>
  );
}

function EndpointRow({ item }: { item: RegistryItem }) {
  const acc = item.access ?? [];
  return (
    <tr>
      <td className="mono">{item.url}</td>
      <td className="mono reg-res-cell" title={item.resource}>
        {item.registry_id}
      </td>
      <td>
        {item.access_error ? (
          <span className="reg-access-err">{item.access_error}</span>
        ) : acc.length === 0 ? (
          <span className="reg-deny">
            <CloseIcon size={12} /> No one — default deny
          </span>
        ) : (
          acc.map((a, i) => (
            <span key={i} className="reg-ep-who" title={a.member}>
              <CheckIcon size={12} /> {a.member_label}
              {a.condition && <span className="reg-cond-tag cond">conditional</span>}
            </span>
          ))
        )}
      </td>
    </tr>
  );
}

function Endpoints({ view }: { view: RegistryView }) {
  const [open, setOpen] = useState(false);
  const items = view.endpoints.items;
  const holders = new Set(items.flatMap((e) => (e.access ?? []).map((a) => a.member_label)));
  return (
    <div className="reg-eps">
      <p className="sheet-explain">
        Google APIs an Agent Runtime agent needs (Vertex AI, logging, tracing…). With the egress gateway attached everything else is denied, so these
        are registered as endpoints and allowed for every Agent Identity agent in the project.
      </p>
      <button type="button" className="reg-eps-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronIcon size={16} />
        <strong>{view.endpoints.count}</strong> platform endpoint{view.endpoints.count === 1 ? "" : "s"}
        {holders.size > 0 && <span className="muted"> · callable by {[...holders].join(", ")}</span>}
      </button>
      {open && (
        <div className="gw-table-wrap">
          <table className="gw-table reg-ep-table">
            <thead>
              <tr>
                <th scope="col">Host</th>
                <th scope="col">Registry id</th>
                <th scope="col">Who can call it</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <EndpointRow key={e.id} item={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Glass sheet: Agents / MCP servers / Platform endpoints, refreshed every 10 s while open. */
export function RegistrySheet({ d, api, onClose }: { d: Demo; api: Api; onClose: () => void }) {
  const { themeId, mode, theme } = d;
  const [view, setView] = useState<RegistryView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("agents");
  const seq = useRef(0);

  const load = useCallback(
    async (refresh = false) => {
      if (!themeId) return;
      const my = ++seq.current;
      setLoading(true);
      try {
        const v = await api.registry(themeId, mode, refresh);
        if (my !== seq.current) return;
        setView(v);
        setErr(null);
      } catch (e) {
        if (my === seq.current) setErr((e as Error).message ?? String(e));
      } finally {
        if (my === seq.current) setLoading(false);
      }
    },
    [api, themeId, mode],
  );

  useEffect(() => {
    void load();
    const iv = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => clearInterval(iv);
  }, [load]);

  const tabs: { id: Tab; label: string; n: number | undefined }[] = [
    { id: "agents", label: "Agents", n: view?.agents.length },
    { id: "mcp", label: "MCP servers", n: view?.mcp_servers.length },
    { id: "endpoints", label: "Platform endpoints", n: view?.endpoints.count },
  ];
  const simulated = view?.source === "simulated";
  return (
    <GlassModal
      className="sheet-log sheet-registry"
      icon={<RegistryIcon size={18} />}
      title="Agent Registry"
      subtitle={
        <>
          {theme?.name}
          {view && <span className={`tag ${simulated ? "tag-sim" : "tag-green"}`}>{simulated ? "Simulated" : "Live"}</span>}
          {view && <span>· updated {fmtTime(Date.parse(view.fetched_at))}</span>}
          <span className="live-dot" title="Refreshes every 10 seconds">
            {loading ? "refreshing" : "auto"}
          </span>
        </>
      }
      actions={
        <>
          <button type="button" className="glass-btn" onClick={() => void load(true)} disabled={loading} aria-label="Refresh the Agent Registry view now">
            <RefreshIcon size={15} />
            <span>Refresh</span>
          </button>
          {view?.console_url && (
            <a className="glass-btn" href={view.console_url} target="_blank" rel="noopener noreferrer" aria-label="Open Agent Registry in the Cloud Console (new tab)">
              Open in Cloud Console <OpenInNewIcon size={13} />
            </a>
          )}
        </>
      }
      onClose={onClose}
    >
      <div className="reg-tabs" role="tablist" aria-label="Registry entry kinds">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`reg-tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
            {t.n !== undefined && <span className="reg-tab-n">{t.n}</span>}
          </button>
        ))}
      </div>
      <div className="reg-body" role="tabpanel">
        {simulated && (
          <p className="gw-note">
            Simulated from the theme files and the policies applied in this mode{view?.error ? ` (${view.error})` : ""}. In Live mode this reads Agent Registry
            and each entry's IAP policy.
          </p>
        )}
        {!simulated && view?.error && <div className="policy-error">Partly unavailable: {view.error}</div>}
        {err && <div className="policy-error">Couldn't load Agent Registry: {err}</div>}
        {!view && !err && <div className="muted">Loading…</div>}
        {view && tab === "agents" && (
          <div className="reg-grid">
            {view.agents.map((it) => (
              <ItemCard key={it.id} item={it} />
            ))}
          </div>
        )}
        {view && tab === "mcp" && (
          <div className="reg-grid">
            {view.mcp_servers.map((it) => (
              <ItemCard key={it.id} item={it} />
            ))}
          </div>
        )}
        {view && tab === "endpoints" && <Endpoints view={view} />}
      </div>
    </GlassModal>
  );
}
