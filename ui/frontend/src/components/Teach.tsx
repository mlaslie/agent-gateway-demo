// Teaching aids: glossary terms (dotted underline, plain explanation on hover / focus / tap), the failed-call
// "why / fix" line, and the "How it works" card. Data lives in src/teach/ (pure modules with node tests).
import { createContext, Fragment, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Theme } from "../api/types";
import { glossaryEntry, glossSegments } from "../teach/glossary";
import type { Decoded } from "../teach/decode";
import { GlassModal } from "./Glass";
import { ArrowIcon, CodeIcon, HelpIcon, LockIcon } from "./Icons";
import { openBuildByHand } from "../build/route";

// ---------- context: what the decoder needs to know about the current theme and policy state ----------

export interface TeachInfo {
  theme: Theme | null;
  /** Ids of the policies that are applied or being applied. */
  active: ReadonlySet<string>;
}
export const TeachContext = createContext<TeachInfo>({ theme: null, active: new Set() });
export const useTeach = () => useContext(TeachContext);

// ---------- glossary term ----------

const GAP = 8;

/** A glossary term: dotted underline; hover, keyboard focus or a tap shows its one-line explanation. */
export function Term({ id, children }: { id: string; children?: ReactNode }) {
  const g = glossaryEntry(id);
  const descId = useId();
  const ref = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const closeT = useRef<number | undefined>(undefined);

  const openedAt = useRef(0);
  const show = useCallback(() => {
    window.clearTimeout(closeT.current);
    setOpen((v) => {
      if (!v) openedAt.current = Date.now();
      return true;
    });
  }, []);
  const hideSoon = useCallback(() => {
    window.clearTimeout(closeT.current);
    closeT.current = window.setTimeout(() => setOpen(false), 120);
  }, []);
  useEffect(() => () => window.clearTimeout(closeT.current), []);

  const place = useCallback(() => {
    const el = ref.current;
    const t = tip.current;
    if (!el || !t) return;
    const r = el.getBoundingClientRect();
    const vh = document.documentElement.clientHeight;
    if (r.bottom < 0 || r.top > vh) return setOpen(false); // scrolled out of view
    const w = t.offsetWidth;
    const h = t.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const left = Math.min(Math.max(GAP, r.left + r.width / 2 - w / 2), vw - w - GAP);
    const top = r.top - h - 6 >= GAP ? r.top - h - 6 : r.bottom + 6;
    setPos({ left, top });
  }, []);

  useLayoutEffect(() => {
    if (!open) setPos(null);
    else place();
  }, [open, place]);

  // Close on Escape (anywhere); follow the term when something scrolls (the tip is fixed-position).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        e.stopPropagation();
      }
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  if (!g) return <>{children}</>;
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="term nodrag nopan"
        aria-describedby={descId}
        onMouseEnter={show}
        onMouseLeave={hideSoon}
        onFocus={show}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          // Tap to toggle on touch screens; never trigger the surrounding label / node / row.
          e.preventDefault();
          e.stopPropagation();
          // A tap also fires hover and focus, which just opened it: keep it open then.
          if (Date.now() - openedAt.current > 400) setOpen((v) => !v);
        }}
      >
        {children ?? g.term}
      </button>
      {/* Description for screen readers (hidden elements still count for aria-describedby). */}
      <span id={descId} hidden>
        {g.text}
      </span>
      {open &&
        createPortal(
          <span
            ref={tip}
            className="term-tip"
            aria-hidden="true"
            style={pos ? { left: pos.left, top: pos.top } : { left: -9999, top: 0, visibility: "hidden" }}
            onMouseEnter={show}
            onMouseLeave={hideSoon}
          >
            <b>{g.term}</b> {g.text}
          </span>,
          document.body,
        )}
    </>
  );
}

/** Plain text with its glossary terms marked (each term once). */
export function Glossed({ text, seen }: { text: string | undefined | null; seen?: Set<string> }) {
  if (!text) return null;
  return (
    <>
      {glossSegments(text, seen).map((s, i) =>
        typeof s === "string" ? (
          <Fragment key={i}>{s}</Fragment>
        ) : (
          <Term key={i} id={s.id}>
            {s.text}
          </Term>
        ),
      )}
    </>
  );
}

// ---------- error decoder line ----------

export function WhyFix({ d, className = "" }: { d: Decoded; className?: string }) {
  return (
    <div className={`whyfix ${className}`} role="note" aria-label={`${d.title}. Why: ${d.why} Fix: ${d.fix}`}>
      <div className="wf-row" aria-hidden="true">
        <span className="wf-k">Why</span>
        <span>{d.why}</span>
      </div>
      <div className="wf-row" aria-hidden="true">
        <span className="wf-k wf-fix">Fix</span>
        <span>{d.fix}</span>
      </div>
    </div>
  );
}

/** Plain text of a decoded outcome, for tooltips (title attributes). */
export const whyFixText = (d: Decoded) => `${d.title}\nWhy: ${d.why}\nFix: ${d.fix}`;

// ---------- "How it works" ----------

export function HelpButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="how-btn" onClick={onClick} title="How it works: the call path in one picture, and how to build it by hand">
      <span className="how-btn-icon" aria-hidden="true">
        <HelpIcon size={16} />
      </span>
      <span className="how-btn-text">How it works</span>
    </button>
  );
}

function Step({ name, term, line, tone }: { name: string; term?: string; line: ReactNode; tone: string }) {
  return (
    <li className={`hw-step hw-${tone}`}>
      <div className="hw-name">{term ? <Term id={term}>{name}</Term> : name}</div>
      <div className="hw-line">{line}</div>
    </li>
  );
}

const Arrow = () => (
  <li className="hw-arrow" aria-hidden="true">
    <ArrowIcon size={16} />
  </li>
);

export function HowItWorks({ privateNetworking, onClose }: { privateNetworking: boolean; onClose: () => void }) {
  return (
    <GlassModal className="sheet-how" icon={<HelpIcon size={18} />} title="How it works" subtitle="Every call your agent makes, in one picture" onClose={onClose}>
      <ol className="hw-flow" aria-label="Call path">
        <Step name="Agent" tone="agent" line={<>Your agent, with its own <Term id="agent-identity">Agent Identity</Term> (its badge).</>} />
        <Arrow />
        <Step name="Agent Gateway" term="agent-gateway" tone="gateway" line="The checkpoint: every call from the agent goes through it." />
        <Arrow />
        <li className="hw-checks">
          <ul aria-label="The two checks">
            <Step name="Agent Registry" term="agent-registry" tone="registry" line="The address book: is the destination listed?" />
            <Step name="IAP grant" term="egressor" tone="grant" line={<>The permission slip: does this agent hold <span className="mono">roles/iap.egressor</span> on it?</>} />
          </ul>
        </li>
        <Arrow />
        <Step name="Destination" tone="dest" line="A2A agent, MCP server or API. Reached only when both checks pass." />
      </ol>
      {privateNetworking && (
        <>
          <div className="hw-sub">
            <LockIcon size={12} /> Private Networking adds a private route after the gateway
          </div>
          <ol className="hw-flow hw-flow-pn" aria-label="Private route">
            <Step name="Network attachment" term="network-attachment" tone="vpc" line="The gateway's door into your VPC." />
            <Arrow />
            <Step name="PSC endpoint" term="psc-endpoint" tone="vpc" line="A private IP that leads to Google services such as Cloud Run." />
            <Arrow />
            <Step name="Private service" tone="dest" line="Cloud Run with ingress internal: only reachable this way (404 from the internet)." />
          </ol>
        </>
      )}
      <p className="hw-rule">
        <b>
          <Term id="default-deny">Default deny</Term>:
        </b>{" "}
        not registered, or no grant, means 403. A registry entry{privateNetworking ? " or a route" : ""} isn't permission.
      </p>
      <div className="hw-foot">
        <button
          type="button"
          className="glass-btn"
          onClick={() => {
            onClose();
            openBuildByHand();
          }}
          title="Every step of the build, in order, as curl, gcloud, Terraform and Python"
        >
          <CodeIcon size={15} /> Build it by hand
        </button>
      </div>
    </GlassModal>
  );
}
