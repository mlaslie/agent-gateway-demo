// Checks the "Build it by hand" snippets: every timeline step exists with all four tools, the shell tabs parse in
// bash and zsh (macOS's default), the Python tabs parse, and the Terraform tabs are valid HCL. Tools that aren't
// installed are skipped. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { MODEL, S, TL, orderHtml, pnIntroHtml, sourcesHtml } from "./data.js";

const TOOLS = ["curl", "gcloud", "tf", "py"] as const;
const has = (cmd: string) => spawnSync("sh", ["-c", `command -v ${cmd}`]).status === 0;
const run = (cmd: string, args: string[], input: string) => spawnSync(cmd, args, { input, encoding: "utf8" });
const tabs = (k: (typeof TOOLS)[number]) =>
  Object.entries(S)
    .map(([id, s]) => [id, s.tabs[k]?.code ?? ""] as const)
    .filter(([, code]) => code.trim() !== "");

test("every timeline step exists and has all four tools", () => {
  for (const view of ["internet", "pn"] as const)
    for (const p of TL[view])
      for (const id of p.steps) {
        assert.ok(S[id], `${view}: unknown step ${id}`);
        for (const k of TOOLS) {
          const t = S[id].tabs[k];
          assert.ok(t, `${id}: no ${k} tab`);
          assert.ok(t.code || t.note, `${id}/${k}: neither code nor a note`);
        }
      }
});

test('every "Show code" button in the static sections points at a step', () => {
  for (const html of [orderHtml(), pnIntroHtml(), sourcesHtml()])
    for (const m of html.matchAll(/data-step="([^"]+)"/g)) assert.ok(S[m[1]], `unknown step ${m[1]}`);
  assert.ok(MODEL.internet.length && MODEL.pn.length);
});

test('no "$VAR:x" (zsh reads :g / :s / :h after a variable as a modifier); use "${VAR}:x"', () => {
  for (const k of ["curl", "gcloud"] as const)
    for (const [id, code] of tabs(k)) assert.doesNotMatch(code, /\$[A-Za-z_]\w*:[A-Za-z]/, `${id}/${k}`);
});

test("no comment after a line-continuation backslash (it silently ends the command)", () => {
  for (const k of ["curl", "gcloud"] as const)
    for (const [id, code] of tabs(k)) assert.doesNotMatch(code, /\\[ \t]+#/, `${id}/${k}`);
});

for (const sh of ["bash", "zsh"])
  test(`shell tabs parse in ${sh}`, { skip: !has(sh) && `${sh} not installed` }, () => {
    for (const k of ["curl", "gcloud"] as const)
      for (const [id, code] of tabs(k)) {
        const r = run(sh, ["-n"], code);
        assert.equal(r.status, 0, `${id}/${k}: ${r.stderr}`);
      }
  });

test("Python tabs parse", { skip: !has("python3") && "python3 not installed" }, () => {
  for (const [id, code] of tabs("py")) {
    const r = run("python3", ["-I", "-c", "import ast, sys; ast.parse(sys.stdin.read())"], code);
    assert.equal(r.status, 0, `${id}/py: ${r.stderr}`);
  }
});

test("Terraform tabs are valid HCL", { skip: !has("terraform") && "terraform not installed" }, () => {
  for (const [id, code] of tabs("tf")) {
    const r = run("terraform", ["fmt", "-"], code);
    assert.equal(r.status, 0, `${id}/tf: ${r.stderr}`);
  }
});
