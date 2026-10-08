// Light syntax highlighting for the "Under the hood" lines: shell (gcloud/curl), HTTP verbs, URLs, JSON.
import type { ReactNode } from "react";

const TOKEN =
  /(https?:\/\/[^\s"'\\]+)|("(?:[^"\\]|\\.)*")|('(?:[^'\\]|\\.)*')|((?:^|(?<=\s))--?[A-Za-z][\w.-]*)|\b(gcloud|curl|gsutil|bq|kubectl|uv|python3?)\b|\b(GET|POST|PATCH|PUT|DELETE)\b|\b(true|false|null)\b|(\\$)|(\$\([^)]*\))/g;

function highlight(line: string): ReactNode[] {
  if (/^\s*#/.test(line)) return [<span key="c" className="tk-comment">{line}</span>];
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(line))) {
    if (m[0] === "") {
      TOKEN.lastIndex++;
      continue;
    }
    if (m.index > last) out.push(line.slice(last, m.index));
    const [, url, dq, sq, flag, cmd, verb, lit, cont, sub] = m;
    let cls = "";
    if (url) cls = "tk-url";
    else if (dq) cls = /^\s*:/.test(line.slice(m.index + dq.length)) ? "tk-key" : "tk-str";
    else if (sq) cls = "tk-str";
    else if (flag) cls = "tk-flag";
    else if (cmd) cls = "tk-cmd";
    else if (verb) cls = "tk-verb";
    else if (lit) cls = "tk-lit";
    else if (cont) cls = "tk-cont";
    else if (sub) cls = "tk-sub";
    out.push(
      <span key={m.index} className={cls}>
        {m[0]}
      </span>,
    );
    last = m.index + m[0].length;
  }
  if (last < line.length) out.push(line.slice(last));
  return out;
}

/** Pretty-print a line that is a bare JSON object/array (expanded view only). */
function expandJson(line: string): string[] {
  const t = line.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return [line];
  try {
    return JSON.stringify(JSON.parse(t), null, 2).split("\n");
  } catch {
    return [line];
  }
}

/** Inline (compact): one row per line, long lines truncated with an ellipsis (full text in the tooltip). */
export function CodeInline({ lines }: { lines: string[] }) {
  return (
    <div className="code code-inline" role="region" aria-label="Commands and API calls (truncated)">
      {lines.map((l, i) => (
        <div key={i} className="code-row" title={l.length > 60 ? l : undefined}>
          {highlight(l)}
        </div>
      ))}
    </div>
  );
}

/** Expanded: everything, wrapped, JSON lines pretty-printed, with line numbers. */
export function CodeFull({ lines }: { lines: string[] }) {
  const rows = lines.flatMap(expandJson);
  return (
    <pre className="code code-full" aria-label="Commands and API calls">
      {rows.map((l, i) => (
        <div key={i} className="code-line">
          <span className="code-ln" aria-hidden="true">
            {i + 1}
          </span>
          <span className="code-src">{highlight(l)}</span>
        </div>
      ))}
    </pre>
  );
}
