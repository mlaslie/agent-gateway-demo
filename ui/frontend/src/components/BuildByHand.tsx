// "Build it by hand": a full-page view of everything ./agdemo up and the UI toggles create, as a timeline
// (Internet and Private Networking) with a plain explanation per step and the same step as curl / gcloud /
// Terraform / Python. Opened from "How it works" (or #build-by-hand). Data: src/build/data.js.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject } from "react";
import { MODEL, S, TL, orderHtml, pnIntroHtml, sourcesHtml, type ModelItem, type Step, type ToolKey, type View } from "../build/data.js";
import { CopyButton, GlassModal } from "./Glass";
import { ArrowIcon, CodeIcon } from "./Icons";

type PageView = View | "src";
const VIEWS: { id: PageView; label: string }[] = [
  { id: "internet", label: "Internet" },
  { id: "pn", label: "Private Networking (VPC)" },
  { id: "src", label: "Sources → destinations" },
];
const TOOLS: { id: ToolKey; label: string }[] = [
  { id: "curl", label: "curl / REST" },
  { id: "gcloud", label: "gcloud" },
  { id: "tf", label: "Terraform" },
  { id: "py", label: "Python SDK" },
];
const VIEW_KEY = "bbh-view";

/** Trusted, hand-written HTML from data.js. */
const html = (s: string) => ({ __html: s });

function storedView(fallback: PageView): PageView {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v === "internet" || v === "pn" || v === "src") return v;
  } catch {
    /* storage blocked */
  }
  return fallback;
}

export function BuildByHand({ privateNetworking, onBack }: { privateNetworking: boolean; onBack: () => void }) {
  const [view, setView] = useState<PageView>(() => storedView(privateNetworking ? "pn" : "internet"));
  const [pinned, setPinned] = useState<string | null>(null);
  const [code, setCode] = useState<{ id: string; n?: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const choose = (v: PageView) => {
    setView(v);
    setPinned(null);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* storage blocked */
    }
  };

  useEffect(() => {
    document.title = "Build it by hand · Agent Gateway demo";
    root.current?.focus({ preventScroll: true });
    return () => {
      document.title = "Agent Gateway & Agent Registry";
    };
  }, []);

  // Esc unpins a tooltip (the code sheet handles its own Esc).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !code) setPinned(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [code]);

  // "Show code" buttons inside the static HTML sections (data-step attributes); a click elsewhere unpins.
  const onClick = (e: ReactMouseEvent) => {
    const t = e.target as HTMLElement;
    const show = t.closest<HTMLElement>(".bb-show[data-step]");
    if (show) {
      setCode({ id: show.dataset.step!, n: show.dataset.n ? Number(show.dataset.n) : undefined });
      return;
    }
    if (!t.closest(".bb-tiphost")) setPinned(null);
  };

  const onTabKey = (e: ReactKeyboardEvent, i: number) => {
    let j: number | null = null;
    if (e.key === "ArrowRight") j = (i + 1) % VIEWS.length;
    if (e.key === "ArrowLeft") j = (i - 1 + VIEWS.length) % VIEWS.length;
    if (e.key === "Home") j = 0;
    if (e.key === "End") j = VIEWS.length - 1;
    if (j === null) return;
    e.preventDefault();
    tabRefs.current[j]?.focus();
    choose(VIEWS[j].id);
  };

  const tip = { pinned, setPinned };
  let n = 0;
  return (
    <div className="bbh" ref={root} tabIndex={-1} onClick={onClick}>
      <header className="bb-header">
        <button type="button" className="glass-btn bb-back" onClick={onBack} title="Back to the demo">
          <ArrowIcon size={16} className="bb-back-icon" />
          <span>Back to the demo</span>
        </button>
        <div className="bb-head-title">
          <div className="bb-eyebrow">Build it yourself</div>
          <h1>How to build this by hand, in order</h1>
        </div>
        <div className="bb-segmented" role="tablist" aria-label="Which build to show">
          {VIEWS.map((v, i) => (
            <button
              key={v.id}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`bb-tab-${v.id}`}
              aria-controls="bb-view"
              aria-selected={view === v.id}
              tabIndex={view === v.id ? 0 : -1}
              className={`bb-seg ${view === v.id ? "on" : ""}`}
              onClick={() => choose(v.id)}
              onKeyDown={(e) => onTabKey(e, i)}
            >
              {v.label}
            </button>
          ))}
        </div>
      </header>

      <main className="bb-main">
        <p className="bb-lede">
          Everything the demo's <code>./agdemo up</code> and the UI toggles create, as a timeline you can follow yourself. Hover, focus or click <b>Why here?</b> on a step for a
          plain-English explanation. <b>Show code</b> gives the same step as curl / REST, gcloud, Terraform and the Python SDK. Placeholders are in CAPITALS (<code>PROJECT_ID</code>,{" "}
          <code>PROJECT_NUMBER</code>, <code>REGION</code>, <code>ORG_ID</code> …).
        </p>

        <section className="bb-card bb-model" aria-labelledby="bb-model-h">
          <h2 id="bb-model-h">The mental model</h2>
          <ModelRow label="Every governed call" items={MODEL.internet} tip={tip} />
          <ModelRow label="Private Networking adds a private route (policy doesn't change)" items={MODEL.pn} tip={tip} />
        </section>

        <div className="bb-legend" aria-label="Legend">
          <span className="bb-lg">
            <span className="bb-who bb-tf">Terraform foundation</span> created once by <code>./agdemo up</code>
          </span>
          <span className="bb-lg">
            <span className="bb-who bb-cli">deploy-theme</span> the CLI, from code
          </span>
          <span className="bb-lg">
            <span className="bb-who bb-toggle">Demo toggle</span> a live switch in the UI
          </span>
          <span className="bb-lg">
            <span className="bb-who bb-hand">You, by hand</span> checks and reads
          </span>
          <span className="bb-lg">
            <span className="bb-unv">unverified</span> documented or plausible, not run in this demo
          </span>
          <span className="bb-lg">
            <span className="bb-time">~5 min</span> typical wait
          </span>
        </div>

        <div id="bb-view" role="tabpanel" aria-labelledby={`bb-tab-${view}`}>
          {view === "src" ? (
            <div dangerouslySetInnerHTML={html(sourcesHtml())} />
          ) : (
            <>
              <div dangerouslySetInnerHTML={html(view === "internet" ? orderHtml() : pnIntroHtml())} />
              <div className="bb-timeline">
                {TL[view].map((p) => (
                  <section key={p.name} className={`bb-phase ${p.optional ? "bb-optional" : ""}`} aria-label={p.name}>
                    <div className="bb-phase-head">
                      <span className="bb-phase-dot" aria-hidden="true" />
                      <div className="bb-phase-name">{p.name}</div>
                      <div className="bb-phase-note">{p.note}</div>
                    </div>
                    <div className="bb-steps">
                      {p.steps.map((id) => {
                        const num = ++n;
                        return <StepCard key={`${id}-${num}`} id={id} n={num} step={S[id]} tip={tip} onCode={() => setCode({ id, n: num })} />;
                      })}
                    </div>
                  </section>
                ))}
              </div>
            </>
          )}
        </div>
      </main>

      {code && S[code.id] && <CodeSheet step={S[code.id]} n={code.n} onClose={() => setCode(null)} />}
    </div>
  );
}

type TipCtl = { pinned: string | null; setPinned: (v: string | null) => void };

function TipBody({ id, what, why, extra }: { id: string; what: string; why?: string; extra?: string }) {
  return (
    <div className="bb-tip" role="tooltip" id={id}>
      <h4>What it does</h4>
      <p dangerouslySetInnerHTML={html(what)} />
      {why && (
        <>
          <h4>Why it comes here</h4>
          <p dangerouslySetInnerHTML={html(why)} />
        </>
      )}
      {extra && (
        <p>
          <span className="bb-unv">unverified</span> <span dangerouslySetInnerHTML={html(extra)} />
        </p>
      )}
    </div>
  );
}

/** Keeps a model-strip tooltip inside the viewport (step tooltips span their card). */
function useKeepInView(host: RefObject<HTMLElement | null>, active: boolean) {
  const place = useCallback(() => {
    const tip = host.current?.querySelector<HTMLElement>(":scope > .bb-tip");
    if (!tip) return;
    tip.style.left = "";
    const r = tip.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    let shift = 0;
    if (r.right > vw - 16) shift = vw - 16 - r.right;
    if (r.left + shift < 16) shift = 16 - r.left;
    if (shift) tip.style.left = `${shift}px`;
  }, [host]);
  useLayoutEffect(() => {
    if (active) place();
  }, [active, place]);
  return place;
}

function ModelRow({ label, items, tip }: { label: string; items: ModelItem[]; tip: TipCtl }) {
  return (
    <div className="bb-model-row">
      <span className="bb-model-label">{label}</span>
      {items.map((m, i) => {
        if ("a" in m)
          return (
            <span key={i} className="bb-arrow" aria-hidden="true">
              {m.a}
            </span>
          );
        if ("p" in m)
          return (
            <span key={i} className="bb-plus" aria-hidden="true">
              {m.p}
            </span>
          );
        if ("rule" in m)
          return (
            <span key={i} className="bb-rule">
              {m.rule}
            </span>
          );
        return <ModelNode key={i} item={m} tipKey={`${label}-${i}`} tip={tip} />;
      })}
    </div>
  );
}

function ModelNode({ item, tipKey, tip }: { item: Extract<ModelItem, { k: string }>; tipKey: string; tip: TipCtl }) {
  const id = useId();
  const host = useRef<HTMLSpanElement>(null);
  const on = tip.pinned === tipKey;
  const place = useKeepInView(host, on);
  return (
    <span ref={host} className={`bb-mnode bb-tiphost bb-k-${item.k} ${on ? "pinned" : ""}`} onPointerEnter={place} onFocus={place}>
      <button type="button" className="bb-why" aria-expanded={on} aria-describedby={id} onClick={() => tip.setPinned(on ? null : tipKey)}>
        {item.t} <small>{item.s}</small>
      </button>
      <TipBody id={id} what={item.what} why={item.why} />
    </span>
  );
}

function StepCard({ id, n, step, tip, onCode }: { id: string; n: number; step: Step; tip: TipCtl; onCode: () => void }) {
  const tid = useId();
  const key = `step-${id}-${n}`;
  const on = tip.pinned === key;
  return (
    <article className={`bb-step bb-tiphost ${on ? "pinned" : ""}`}>
      <div className="bb-step-top">
        <span className="bb-num" aria-hidden="true">
          {n}
        </span>
        <div className="bb-step-text">
          <div className="bb-step-title">
            <span className="bb-sr">Step {n}: </span>
            {step.title}
          </div>
          <div className="bb-step-sub">{step.sub}</div>
        </div>
      </div>
      <div className="bb-step-meta">
        <span className={`bb-who bb-${step.who[0]}`}>{step.who[1]}</span>
        {step.time && step.time !== "—" && <span className="bb-time">{step.time}</span>}
        {step.unv && <span className="bb-unv">unverified</span>}
      </div>
      <div className="bb-step-actions">
        <button type="button" className="bb-btn bb-why" aria-expanded={on} aria-describedby={tid} onClick={() => tip.setPinned(on ? null : key)}>
          Why here?
        </button>
        <button type="button" className="bb-btn bb-btn-primary" onClick={onCode}>
          <CodeIcon size={14} /> Show code
        </button>
      </div>
      <TipBody id={tid} what={step.what} why={step.why} extra={step.unv} />
    </article>
  );
}

/** Comment lines (and trailing "# ..." comments outside quotes) in the code's muted style. */
function CodeLines({ code }: { code: string }) {
  return (
    <>
      {code.split("\n").map((l, i) => {
        let body: ReactNode = l;
        if (/^\s*(#|\/\/)/.test(l)) body = <span className="bb-c">{l}</span>;
        else {
          const m = /(\s)(#\s.*)$/.exec(l);
          if (m && !/['"]/.test(m[2])) body = (
            <>
              {l.slice(0, m.index + 1)}
              <span className="bb-c">{m[2]}</span>
            </>
          );
        }
        return (
          <span key={i} className="bb-line">
            {body}
            {"\n"}
          </span>
        );
      })}
    </>
  );
}

function CodeSheet({ step, n, onClose }: { step: Step; n?: number; onClose: () => void }) {
  const [tool, setTool] = useState<ToolKey>("curl");
  // Phones wrap long lines by default; wider screens keep the shape of the code and scroll inside the box.
  const [wrap, setWrap] = useState(() => !!window.matchMedia?.("(max-width: 600px)").matches);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const t = step.tabs[tool];
  const onKey = (e: ReactKeyboardEvent, i: number) => {
    let j: number | null = null;
    if (e.key === "ArrowRight") j = (i + 1) % TOOLS.length;
    if (e.key === "ArrowLeft") j = (i - 1 + TOOLS.length) % TOOLS.length;
    if (e.key === "Home") j = 0;
    if (e.key === "End") j = TOOLS.length - 1;
    if (j === null) return;
    e.preventDefault();
    refs.current[j]?.focus();
    setTool(TOOLS[j].id);
  };
  return (
    <GlassModal
      className="bb-sheet"
      icon={n ? <span className="bb-num">{n}</span> : <CodeIcon size={18} />}
      title={step.title}
      subtitle={step.sub}
      actions={
        <button type="button" className="glass-btn" aria-pressed={wrap} onClick={() => setWrap((v) => !v)} title="Wrap long lines">
          {wrap ? "No wrap" : "Wrap"}
        </button>
      }
      onClose={onClose}
    >
      <div className="bb-tools" role="tablist" aria-label="Tool">
        {TOOLS.map((x, i) => {
          const tx = step.tabs[x.id];
          return (
            <button
              key={x.id}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`bb-tool-${x.id}`}
              aria-controls="bb-tool-panel"
              aria-selected={tool === x.id}
              tabIndex={tool === x.id ? 0 : -1}
              className={`bb-tool ${tool === x.id ? "on" : ""}`}
              onClick={() => setTool(x.id)}
              onKeyDown={(e) => onKey(e, i)}
            >
              {x.label}
              {tx && (tx.unv || tx.warn) && (
                <>
                  <span className="bb-dot" aria-hidden="true" />
                  <span className="bb-sr">{tx.unv ? " (unverified)" : " (not available)"}</span>
                </>
              )}
            </button>
          );
        })}
      </div>
      <div className="bb-tool-panel" role="tabpanel" id="bb-tool-panel" aria-labelledby={`bb-tool-${tool}`}>
        {t.note ? (
          <div className={`bb-note ${t.warn ? "warn" : ""}`}>
            {t.unv && <span className="bb-unv">unverified</span>} <span dangerouslySetInnerHTML={html(t.note)} />
          </div>
        ) : (
          t.unv && (
            <div className="bb-note warn">
              <span className="bb-unv">unverified</span> Not run in this demo; the demo uses another tab's form for this step.
            </div>
          )
        )}
        {t.code && (
          <div className="bb-codebox">
            <CopyButton getText={() => t.code} ariaLabel={`Copy the ${TOOLS.find((x) => x.id === tool)!.label} code`} className="glass-btn bb-copy" />
            <pre className={`bb-pre ${wrap ? "wrap" : ""}`} tabIndex={0} aria-label={`${TOOLS.find((x) => x.id === tool)!.label} code`}>
              <code>
                <CodeLines code={t.code} />
              </code>
            </pre>
          </div>
        )}
      </div>
    </GlassModal>
  );
}

