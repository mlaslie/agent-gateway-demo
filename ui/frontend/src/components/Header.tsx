import { useState } from "react";
import type { Mode } from "../api/types";
import type { Demo } from "../useDemo";
import { MA_KEY } from "../useDemo";
import { CopyIcon, GatewayIcon, OpenInNewIcon, RefreshIcon, ShieldIcon, CheckIcon } from "./Icons";
import { ConfirmDialog } from "./ConfirmDialog";
import { PendingProgress, SettledPop, useSettled } from "./Progress";
import { HelpButton, HowItWorks, Term } from "./Teach";

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "live", label: "Live", hint: "Real GCP changes and real agent calls" },
  { id: "demo", label: "Demo", hint: "Simulated: no GCP changes, replays recordings" },
  { id: "live_with_fallback", label: "Live + fallback", hint: "Real GCP; replays a recording if a change is pending or a call fails" },
];

export function Header({ d }: { d: Demo }) {
  const [confirm, setConfirm] = useState(false);
  const [how, setHow] = useState(false);
  const cfg = d.config;
  const env = cfg?.environment;
  const liveOk = !!cfg?.live_available;
  const adminTip = d.canAdmin ? undefined : "Read-only viewer — only presenters (admins) can change policies";
  // Sync and Verify read GCP as the UI's service account and update shared pending state: presenters only in Live.
  const liveLocked = !d.canAdmin && d.mode !== "demo";
  const liveTip = "Read-only viewer — Live Sync and Verify are for presenters (Demo mode works for everyone)";
  const maEnabled = !!d.state?.model_armor.enabled;
  const ma = d.state?.model_armor;
  const maPending = ma?.status === "pending";
  const maSettled = useSettled(ma?.status);
  const geUrl = cfg?.gemini_enterprise?.app_url || "";
  const maAvailable = d.state?.model_armor.available !== false && d.themeSummary?.model_armor !== false;
  const region = d.themeSummary?.region || env?.region;
  const pn = d.networkMode === "private_networking";
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-logo">
          <GatewayIcon size={22} />
        </span>
        <div className="brand-text">
          <div className="brand-title">
            <Term id="agent-gateway">Agent Gateway</Term> &amp; <Term id="agent-registry">Agent Registry</Term>
          </div>
          <div className="brand-sub">Gemini Enterprise Agent Platform · governance demo</div>
        </div>
        <HelpButton onClick={() => setHow(true)} />
      </div>
      {how && <HowItWorks privateNetworking={pn} onClose={() => setHow(false)} />}

      {env && (
        <div className={`env-chip ${pn ? "env-pn" : ""}`} title={`Project ${env.project_id} · region ${region} · prefix ${env.prefix}${pn ? " · Private Networking (VPC-enabled gateway)" : ""}`}>
          <span className="env-dot" />
          <span>{env.project_id}</span>
          <span className="env-sep">·</span>
          <span>{region}</span>
        </div>
      )}

      <div className="header-spacer" />

      {d.networkModes.length > 1 && (
        <div className="field">
          <span className="field-label">Network</span>
          <div className="segmented seg-network" role="radiogroup" aria-label="Network mode">
            {(
              [
                { id: "internet", label: "Internet", hint: `Agent Gateway without a VPC; public Cloud Run destinations (${env?.region ?? "base region"})` },
                {
                  id: "private_networking",
                  label: "Private Networking",
                  hint: `VPC-enabled Agent Gateway in ${cfg?.private_networking?.region ?? "the second region"}: attachment, PSC endpoint, private DNS, internal Cloud Run`,
                },
              ] as const
            ).map((m) => (
              <button key={m.id} role="radio" aria-checked={d.networkMode === m.id} className={`seg ${d.networkMode === m.id ? "on" : ""} seg-net-${m.id}`} title={m.hint} onClick={() => d.setNetworkMode(m.id)}>
                {m.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <label className="field">
        <span className="field-label">Theme</span>
        <select className="select" value={d.themeId} onChange={(e) => d.selectTheme(e.target.value)} disabled={!cfg}>
          {d.modeThemes.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
              {!t.deployed && d.mode !== "demo" ? " (not deployed)" : ""}
            </option>
          ))}
        </select>
      </label>

      <div className="field">
        <span className="field-label">Mode</span>
        <div className="segmented" role="radiogroup" aria-label="Run mode">
          {MODES.map((m) => {
            const disabled = m.id !== "demo" && !liveOk;
            return (
              <button
                key={m.id}
                role="radio"
                aria-checked={d.mode === m.id}
                className={`seg ${d.mode === m.id ? "on" : ""} seg-${m.id}`}
                disabled={disabled}
                title={disabled ? `Live GCP access is not available${cfg?.live_unavailable_reason ? `: ${cfg.live_unavailable_reason}` : ""}` : m.hint}
                onClick={() => d.setMode(m.id)}
              >
                {m.label}
              </button>
            );
          })}
        </div>
      </div>

      {maAvailable && <div className="anchor">
        <label className={`ma-toggle ${maEnabled ? "on" : ""} ${maPending ? "pending" : ""}`} title={adminTip ?? "Screen gateway traffic with Model Armor (all themes)"}>
          <input
            type="checkbox"
            aria-label="Model Armor"
            aria-describedby={maPending ? "pp-model-armor" : undefined}
            checked={maEnabled}
            disabled={!d.canAdmin || !d.state}
            onChange={(e) => d.setModelArmor(e.target.checked)}
          />
          <span className="ma-box">
            <ShieldIcon size={16} />
          </span>
          <span>Model Armor</span>
          {maPending && <span className="mini-spinner" />}
        </label>
        {maPending && ma && (
          <PendingProgress
            id="pp-model-armor"
            floating
            what="Model Armor"
            removing={!ma.enabled}
            since={d.pendingSince(MA_KEY, ma.changed_at)}
            kind="model_armor"
          />
        )}
        {!maPending && maSettled && <SettledPop floating status={maSettled} what="Model Armor" />}
      </div>}

      <div className="ge-group">
        <label className={`ma-toggle ge-toggle ${d.geDemo ? "on" : ""}`} title="Send prompts from Gemini Enterprise instead of this UI">
          <input type="checkbox" aria-label="GE Demo: send prompts from Gemini Enterprise instead of this UI" checked={d.geDemo} onChange={(e) => d.setGeDemo(e.target.checked)} />
          <span className="ma-box">
            <CopyIcon size={14} />
          </span>
          <span>GE Demo</span>
        </label>
        {d.geDemo && geUrl && (
          <a className="ge-chip" href={geUrl} target="_blank" rel="noopener noreferrer" aria-label="Open Gemini Enterprise in a new tab" title="Open Gemini Enterprise in a new tab">
            <OpenInNewIcon size={15} />
          </a>
        )}
      </div>

      <button className="btn btn-outline" disabled={!d.canAdmin || !d.theme || d.busy === "reset"} title={adminTip ?? "Remove every policy for this theme and detach the gateways"} onClick={() => setConfirm(true)}>
        <RefreshIcon size={16} />
        {d.busy === "reset" ? "Resetting…" : "Reset"}
      </button>

      <button
        className="btn btn-outline"
        disabled={liveLocked || !d.theme || !!d.busy}
        title={liveLocked ? liveTip : "Re-read every policy and Model Armor from GCP and clear any stuck pending state"}
        aria-label="Sync with GCP"
        onClick={() => d.sync()}
      >
        <RefreshIcon size={16} />
        {d.busy === "sync" ? "Syncing…" : "Sync"}
      </button>

      <button
        className="btn btn-outline"
        disabled={liveLocked || !d.theme || d.busy === "verify" || d.busy === "reset"}
        title={liveLocked ? liveTip : "Check that this theme is back at step 1: no policies, no gateways, Model Armor off, every connection direct"}
        aria-label="Verify the start state"
        onClick={() => d.verify()}
      >
        <CheckIcon size={16} />
        {d.busy === "verify" ? "Verifying…" : "Verify"}
      </button>

      {confirm && (
        <ConfirmDialog
          title="Reset to the start?"
          body={
            <>
              This returns <b>{d.theme?.name}</b> to step 1, Wide open:
              <ul className="confirm-list">
                <li>removes every applied policy and detaches the {pn ? "VPC-enabled egress gateway" : "egress and ingress gateways"}</li>
                {!pn && <li>turns Model Armor off (it applies to all themes)</li>}
                <li>switches to the Wide open tab and clears the activity log</li>
              </ul>
              {d.mode === "demo"
                ? "Simulated state only; nothing changes in GCP."
                : "This makes real changes in GCP. Detaching takes ~2.5 min and Model Armor ~4 min; progress shows on each policy."}
            </>
          }
          confirmLabel="Reset"
          danger={d.mode !== "demo"}
          onCancel={() => setConfirm(false)}
          onConfirm={() => {
            setConfirm(false);
            d.reset();
          }}
        />
      )}
    </header>
  );
}
