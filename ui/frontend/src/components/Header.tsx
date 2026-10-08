import { useState } from "react";
import type { Mode } from "../api/types";
import type { Demo } from "../useDemo";
import { MA_KEY } from "../useDemo";
import { CopyIcon, GatewayIcon, OpenInNewIcon, RefreshIcon, ShieldIcon, CheckIcon } from "./Icons";
import { ConfirmDialog } from "./ConfirmDialog";
import { PendingProgress, SettledPop, useSettled } from "./Progress";

const MA_TYPICAL_FALLBACK_S = 240;

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "live", label: "Live", hint: "Real GCP changes and real agent calls" },
  { id: "demo", label: "Demo", hint: "Simulated: no GCP changes, replays recordings" },
  { id: "live_with_fallback", label: "Live + fallback", hint: "Real GCP; replays a recording if a change is pending or a call fails" },
];

export function Header({ d }: { d: Demo }) {
  const [confirm, setConfirm] = useState(false);
  const cfg = d.config;
  const env = cfg?.environment;
  const liveOk = !!cfg?.live_available;
  const adminTip = d.canAdmin ? undefined : "Read-only viewer — only presenters (admins) can change policies";
  const maEnabled = !!d.state?.model_armor.enabled;
  const ma = d.state?.model_armor;
  const maPending = ma?.status === "pending";
  const maSettled = useSettled(ma?.status);
  const geUrl = cfg?.gemini_enterprise?.app_url || "";
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-logo">
          <GatewayIcon size={22} />
        </span>
        <div className="brand-text">
          <div className="brand-title">Agent Gateway &amp; Agent Registry</div>
          <div className="brand-sub">Gemini Enterprise Agent Platform · governance demo</div>
        </div>
      </div>

      {env && (
        <div className="env-chip" title={`Project ${env.project_id} · region ${env.region} · prefix ${env.prefix}`}>
          <span className="env-dot" />
          <span>{env.project_id}</span>
          <span className="env-sep">·</span>
          <span>{env.region}</span>
        </div>
      )}

      <div className="header-spacer" />

      <label className="field">
        <span className="field-label">Theme</span>
        <select className="select" value={d.themeId} onChange={(e) => d.selectTheme(e.target.value)} disabled={!cfg}>
          {cfg?.themes.map((t) => (
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

      <div className="anchor">
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
            typical={ma.typical_seconds ?? MA_TYPICAL_FALLBACK_S}
          />
        )}
        {!maPending && maSettled && <SettledPop floating status={maSettled} what="Model Armor" />}
      </div>

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
        disabled={!d.theme || !!d.busy}
        title="Re-read every policy and Model Armor from GCP and clear any stuck pending state"
        aria-label="Sync with GCP"
        onClick={() => d.sync()}
      >
        <RefreshIcon size={16} />
        {d.busy === "sync" ? "Syncing…" : "Sync"}
      </button>

      <button
        className="btn btn-outline"
        disabled={!d.theme || d.busy === "verify" || d.busy === "reset"}
        title="Check that this theme is back at step 1: no policies, no gateways, Model Armor off, every connection direct"
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
                <li>removes every applied policy and detaches the egress and ingress gateways</li>
                <li>turns Model Armor off (it applies to all themes)</li>
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
