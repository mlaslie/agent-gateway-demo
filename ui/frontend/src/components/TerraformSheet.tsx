// "Export Terraform" sheet (CONTRACTS §11): HCL for the theme's current policy state, with file tabs,
// an "Include shared infrastructure" toggle, copy and download (.zip of all files).
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Api } from "../api/client";
import type { TerraformExport } from "../api/types";
import type { Demo } from "../useDemo";
import { CopyButton, GlassModal } from "./Glass";
import { CodeIcon, DownloadIcon } from "./Icons";

// ---------------------------------------------------------------- light HCL highlighting
const KEYWORDS = /^(\s*)(resource|data|variable|provider|terraform|locals|output|module|provisioner|required_providers|condition|target|custom_provider|authz_extension|google_managed|spec|deployment_spec|agent_spec|mcp_server_spec|endpoint_spec|interfaces|filter_config|template_metadata|lifecycle)(?=\s*("|\{))/;
const TOKEN = /("(?:[^"\\]|\\.)*")|(#.*$|\/\/.*$)|\b(true|false|null)\b|\b(var|local|each|self|path)\.[\w.[\]"-]*|\b([a-z_][a-z0-9_]*)(?=\()|\b(\d+(?:\.\d+)?)\b|(<<-?\w+)/g;

function strWithInterp(s: string, key: number): ReactNode {
  const parts = s.split(/(\$\{[^}]*\})/g);
  return (
    <span key={key} className="tk-str">
      {parts.map((p, i) => (p.startsWith("${") ? <span key={i} className="tk-interp">{p}</span> : p))}
    </span>
  );
}

function hclLine(line: string): ReactNode[] {
  if (/^\s*#/.test(line)) return [<span key="c" className="tk-comment">{line}</span>];
  const out: ReactNode[] = [];
  let rest = line;
  let offset = 0;
  const kw = KEYWORDS.exec(line);
  if (kw) {
    out.push(kw[1], <span key="kw" className="tk-kw">{kw[2]}</span>);
    offset = kw[1].length + kw[2].length;
    rest = line.slice(offset);
    // block labels: resource "type" "name"
    const labels = /^((?:\s+"[^"]*")+)/.exec(rest);
    if (labels) {
      out.push(<span key="lbl" className="tk-label">{labels[1]}</span>);
      offset += labels[1].length;
      rest = line.slice(offset);
    }
  } else {
    const attr = /^(\s*)([A-Za-z_][\w-]*|"[^"]+")(\s*=\s*)/.exec(line);
    if (attr) {
      out.push(attr[1], <span key="attr" className="tk-attr">{attr[2]}</span>, attr[3]);
      offset = attr[0].length;
      rest = line.slice(offset);
    }
  }
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(rest))) {
    if (m[0] === "") {
      TOKEN.lastIndex++;
      continue;
    }
    if (m.index > last) out.push(rest.slice(last, m.index));
    const [, str, cmt, lit, ref, fn, num, here] = m;
    const k = offset + m.index;
    if (str) out.push(strWithInterp(str, k));
    else if (cmt) out.push(<span key={k} className="tk-comment">{cmt}</span>);
    else if (lit) out.push(<span key={k} className="tk-lit">{lit}</span>);
    else if (ref) out.push(<span key={k} className="tk-ref">{m[0]}</span>);
    else if (fn) out.push(<span key={k} className="tk-fn">{fn}</span>);
    else if (num) out.push(<span key={k} className="tk-lit">{num}</span>);
    else if (here) out.push(<span key={k} className="tk-kw">{here}</span>);
    last = m.index + m[0].length;
  }
  if (last < rest.length) out.push(rest.slice(last));
  return out;
}

function highlight(text: string, file: string): ReactNode[] {
  const lines = text.replace(/\n$/, "").split("\n");
  if (!file.endsWith(".tf")) {
    let fence = false;
    return lines.map((l) => {
      if (/^```/.test(l)) {
        fence = !fence;
        return <span className="tk-comment">{l}</span>;
      }
      if (fence) return /^\s*#/.test(l) ? <span className="tk-comment">{l}</span> : <span className="tk-heredoc">{l}</span>;
      return /^#{1,6} /.test(l) ? <span className="tk-kw">{l}</span> : l;
    });
  }
  let heredoc: string | null = null;
  return lines.map((l) => {
    if (heredoc) {
      if (l.trim() === heredoc) {
        heredoc = null;
        return <span className="tk-kw">{l}</span>;
      }
      return <span className="tk-heredoc">{l}</span>;
    }
    const h = /<<-?(\w+)\s*$/.exec(l);
    if (h && !/^\s*#/.test(l)) heredoc = h[1];
    return hclLine(l);
  });
}

// ---------------------------------------------------------------- tiny .zip writer (STORE, no compression)
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(b: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC_TABLE[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
export function makeZip(files: Record<string, string>, dir: string): Blob {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const d = new Date();
  const dosTime = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const dosDate = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  for (const [name, text] of Object.entries(files)) {
    const fname = enc.encode(`${dir}/${name}`);
    const data = enc.encode(text);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // UTF-8 names
    local.setUint16(8, 0, true); // STORE
    local.setUint16(10, dosTime, true);
    local.setUint16(12, dosDate, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, fname.length, true);
    local.setUint16(28, 0, true);
    parts.push(new Uint8Array(local.buffer), fname, data);
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true);
    cen.setUint16(4, 20, true);
    cen.setUint16(6, 20, true);
    cen.setUint16(8, 0x0800, true);
    cen.setUint16(10, 0, true);
    cen.setUint16(12, dosTime, true);
    cen.setUint16(14, dosDate, true);
    cen.setUint32(16, crc, true);
    cen.setUint32(20, data.length, true);
    cen.setUint32(24, data.length, true);
    cen.setUint16(28, fname.length, true);
    cen.setUint32(42, offset, true);
    central.push(new Uint8Array(cen.buffer), fname);
    offset += 30 + fname.length + data.length;
  }
  const cenSize = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  const count = Object.keys(files).length;
  end.setUint16(8, count, true);
  end.setUint16(10, count, true);
  end.setUint32(12, cenSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)] as BlobPart[], { type: "application/zip" });
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------- sheet
export function TerraformSheet({ d, api, onClose }: { d: Demo; api: Api; onClose: () => void }) {
  const { themeId, mode, theme } = d;
  const [shared, setShared] = useState(false);
  const [data, setData] = useState<TerraformExport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState("main.tf");
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!themeId) return;
    const my = ++seq.current;
    setLoading(true);
    try {
      const r = await api.terraform(themeId, mode, shared);
      if (my !== seq.current) return;
      setData(r);
      setErr(null);
    } catch (e) {
      if (my === seq.current) setErr((e as Error).message ?? String(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [api, themeId, mode, shared]);

  useEffect(() => {
    void load();
  }, [load]);

  const names = data ? Object.keys(data.files) : [];
  const current = data?.files[file] ?? "";
  const rows = useMemo(() => highlight(current, file), [current, file]);
  const simulated = data?.source === "simulated";
  const dir = `agdemo-${themeId}-terraform`;
  return (
    <GlassModal
      className="sheet-log sheet-terraform"
      icon={<CodeIcon size={18} />}
      title="Export Terraform"
      subtitle={
        <>
          {theme?.name}
          {data && <span className={`tag ${simulated ? "tag-sim" : "tag-green"}`}>{simulated ? "Simulated" : "Live"}</span>}
          <span>· hashicorp/google ≥ 8.1.0</span>
          {loading && <span className="mini-spinner" aria-label="Loading" />}
        </>
      }
      actions={
        <>
          <CopyButton ariaLabel={`Copy ${file}`} label={`Copy ${file}`} getText={() => current} />
          <button
            type="button"
            className="glass-btn"
            disabled={!data}
            onClick={() => data && download(makeZip(data.files, dir), `${dir}.zip`)}
            aria-label="Download all files as a .zip"
            title={`Download ${names.join(", ")} as ${dir}.zip`}
          >
            <DownloadIcon size={15} />
            <span>Download .zip</span>
          </button>
        </>
      }
      onClose={onClose}
    >
      <div className="tf-bar">
        <div className="reg-tabs tf-tabs" role="tablist" aria-label="Terraform files">
          {(names.length ? names : ["main.tf", "variables.tf", "README.md"]).map((n) => (
            <button key={n} type="button" role="tab" aria-selected={file === n} className={`reg-tab mono ${file === n ? "on" : ""}`} onClick={() => setFile(n)}>
              {n}
            </button>
          ))}
        </div>
        <label className={`llm-toggle ${shared ? "on" : ""}`} title="Also emit the gateways, authz extensions, Model Armor template, Agent Registry entries and IAM that ./agdemo bootstrap and deploy-theme create">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          Include shared infrastructure
        </label>
      </div>
      {data && (
        <div className="tf-notes">
          <div>
            <b>Policy state:</b> <span className="mono">{data.state_line}</span>
          </div>
          <div className="muted">
            {data.include_shared
              ? "Managed: policies + shared infrastructure (gateways, authz extensions, Model Armor template, Agent Registry entries, IAM)."
              : "Managed: policies only. Shared infrastructure from ./agdemo bootstrap is referenced by name."}{" "}
            Referenced, not managed: the Agent Runtime engine and Cloud Run services (./agdemo deploy-theme).
          </div>
          {data.error && <div className="muted">Live unavailable ({data.error}); exported from the Demo-mode state.</div>}
        </div>
      )}
      <div className="reg-body" role="tabpanel" aria-label={file}>
        {err && <div className="policy-error">Couldn't generate Terraform: {err}</div>}
        {!data && !err && <div className="muted">Generating…</div>}
        {data && (
          <pre className="code code-full tf-code" aria-label={`${file} contents`}>
            {rows.map((l, i) => (
              <div key={i} className="code-line">
                <span className="code-ln" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="code-src">{l}</span>
              </div>
            ))}
          </pre>
        )}
      </div>
    </GlassModal>
  );
}
