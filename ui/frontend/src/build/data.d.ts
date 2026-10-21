// Types for data.js (the "Build it by hand" page data).

export type ToolKey = "curl" | "gcloud" | "tf" | "py";
export type View = "internet" | "pn";

export interface Tab {
  code: string;
  /** Trusted HTML shown above the code. */
  note?: string | null;
  /** Documented or plausible, not run in this demo. */
  unv?: boolean;
  /** This tool has no equivalent for the step (the note says what to use instead). */
  warn?: boolean;
}

export interface Step {
  title: string;
  sub: string;
  /** [css class, label]: tf | cli | toggle | hand */
  who: [string, string];
  time?: string;
  /** Trusted HTML. */
  what: string;
  why?: string;
  /** Step-level "unverified" explanation (trusted HTML). */
  unv?: string;
  tabs: Record<ToolKey, Tab>;
}

export interface Phase {
  name: string;
  note: string;
  optional?: boolean;
  steps: string[];
}

export type ModelItem =
  | { k: string; t: string; s: string; what: string; why: string }
  | { a: string }
  | { p: string }
  | { rule: string };

export const S: Record<string, Step>;
export const TL: Record<View, Phase[]>;
export const MODEL: Record<View, ModelItem[]>;
export function orderHtml(): string;
export function pnIntroHtml(): string;
export function sourcesHtml(): string;
