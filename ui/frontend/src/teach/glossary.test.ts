import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GLOSSARY, glossaryEntry, glossSegments } from "./glossary.ts";

const README = readFileSync(new URL("../../../../README.md", import.meta.url), "utf8");
// README cell text without markdown (**bold**, *italic*, `code`).
const noBold = README.replace(/\*\*|`/g, "");
const plain = `${noBold}\n${noBold.replace(/(?<![\w.])\*(\w[^*\n]*\w)\*(?!\w)/g, "$1")}`;

test("wording comes from the README tables", () => {
  const fromTables = ["agent-gateway", "agent-runtime", "bound", "agent-registry", "egressor", "agent-identity", "iap", "model-armor", "service-account", "network-attachment", "psc-endpoint", "private-dns", "dns-peering", "connectivity-template"];
  for (const id of fromTables) {
    const g = glossaryEntry(id);
    assert.ok(g, id);
    assert.ok(plain.includes(g.text), `${id}: "${g.text}" is not in README.md`);
  }
  // egress / ingress are the two halves of the Agent Gateway row
  for (const id of ["egress", "ingress"]) {
    const t = glossaryEntry(id)!.text;
    assert.ok(plain.toLowerCase().includes(t.toLowerCase()), id);
  }
});

test("every requested term is in the glossary", () => {
  const ids = new Set(GLOSSARY.map((g) => g.id));
  for (const id of ["agent-gateway", "egress", "ingress", "bound", "agent-registry", "egressor", "agent-identity", "iap", "model-armor", "default-deny", "network-attachment", "psc-endpoint", "connectivity-template", "dns-peering", "service-account"])
    assert.ok(ids.has(id), id);
});

const marked = (text: string) => glossSegments(text).filter((s) => typeof s !== "string") as { id: string; text: string }[];

test("marks each term once, keeps the text intact", () => {
  const text = "All of Helpdesk Agent's outbound traffic goes through Agent Gateway (default: deny everything). Agent Gateway again.";
  const segs = glossSegments(text);
  assert.equal(segs.map((s) => (typeof s === "string" ? s : s.text)).join(""), text);
  assert.deepEqual(marked(text).map((m) => m.id), ["agent-gateway", "default-deny"]);
});

test("resource names and Cloud Run ingress aren't marked as the plain words", () => {
  assert.deepEqual(marked("agdemo-egress and agdemo-ingress"), []);
  assert.deepEqual(marked("Payroll MCP runs on Cloud Run with ingress internal"), []);
  assert.deepEqual(marked("Grant roles/iap.egressor on Payroll MCP").map((m) => [m.id, m.text]), [["egressor", "roles/iap.egressor"]]);
  assert.deepEqual(marked("Egress · Agent-to-Anywhere").map((m) => m.id), ["egress"]);
  assert.deepEqual(marked("the ingress gateway screens").map((m) => m.id), ["ingress"]);
});

test("longest match wins at the same position", () => {
  assert.deepEqual(marked("PSC endpoint for Google APIs").map((m) => m.text), ["PSC endpoint for Google APIs"]);
  assert.deepEqual(marked("Agent connectivity template").map((m) => m.text), ["Agent connectivity template"]);
});

test("a shared seen set marks a term once across texts", () => {
  const seen = new Set<string>();
  assert.equal(glossSegments("Model Armor", seen).length, 1);
  assert.deepEqual(glossSegments("Model Armor", seen), ["Model Armor"]);
});
