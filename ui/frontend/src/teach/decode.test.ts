// node --test (no dependencies): npm test, or ui/backend/tests/test_frontend_teach.py under pytest.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DECODED, decodeFailure, edgeFacts, logFacts } from "./decode.ts";

const theme = (id: string) => JSON.parse(readFileSync(new URL(`../api/mockTheme.${id}.json`, import.meta.url), "utf8"));
const helpdesk = theme("helpdesk");
const privnet = theme("privnet");
const code = (f: Parameters<typeof decodeFailure>[0]) => decodeFailure(f)?.code ?? null;

test("successes and pending aren't failures", () => {
  for (const state of ["allowed", "direct", "pending", "unknown"]) assert.equal(code({ state, status: 200 }), null);
});

test("403 Egress request is not authorized: no registry entry or no grant", () => {
  assert.equal(code({ state: "denied", status: 403, detail: "403 from gateway: iap.egressor missing for kb-agent" }), "egress-denied");
  assert.equal(code({ status: 403, detail: "Egress request is not authorized." }), "egress-denied");
  assert.equal(code({ state: "error", detail: "Egress request is not authorized." }), "egress-denied");
  assert.match(DECODED["egress-denied"].fix, /about 2 min/);
});

test("403 while the grant is applied: not in effect yet (wait about 2 min)", () => {
  assert.equal(code({ state: "denied", status: 403, grantApplied: true }), "grant-not-in-effect");
  assert.match(DECODED["grant-not-in-effect"].why, /about 2 min \(up to about 7\)/);
});

test("403 from Model Armor: content blocked, also when only the body says so", () => {
  assert.equal(code({ state: "blocked", status: 403 }), "model-armor");
  assert.equal(code({ state: "denied", status: 403, detail: "Model Armor: Prompt violates content security configurations" }), "model-armor");
  assert.equal(code({ status: 403, detail: '{"result":{"isError":true,"content":"agdemo-model-armor-block"}}' }), "model-armor");
});

test("404 on an internal Cloud Run service: not through the VPC path", () => {
  assert.equal(code({ state: "unreachable", status: 404 }), "internal-404");
  assert.equal(code({ state: "error", status: 404 }), "internal-404");
  assert.match(DECODED["internal-404"].why, /ingress internal/);
});

test("401 on Google API calls behind a gateway: the google-auth pin", () => {
  assert.equal(code({ state: "error", status: 401 }), "token-401");
  assert.equal(code({ state: "error", detail: "401 UNAUTHENTICATED from aiplatform.googleapis.com" }), "token-401");
  assert.match(DECODED["token-401"].fix, /google-auth==2\.60\.0/);
});

test("498 or a blocked platform host: platform endpoints not registered", () => {
  assert.equal(code({ state: "error", status: 498 }), "platform-blocked");
  assert.equal(code({ state: "denied", status: 403, host: "us-east4-aiplatform.googleapis.com" }), "platform-blocked");
  assert.equal(code({ state: "denied", status: 403, host: "logging.googleapis.com:443" }), "platform-blocked");
  assert.match(DECODED["platform-blocked"].fix, /platform_endpoints\(\)/);
});

test("denied host with no Agent Registry entry", () => {
  assert.equal(code({ state: "denied", status: 403, host: "example.com", registered: false }), "unregistered");
});

test("unreachable / timeout", () => {
  assert.equal(code({ state: "error", detail: "ReadTimeout: timed out" }), "unreachable");
  assert.equal(code({ state: "error", detail: "ssl/tls alert handshake failure" }), "unreachable");
  assert.equal(code({ state: "error", status: 503 }), "unreachable");
  assert.equal(code({ state: "error", status: null }), "unreachable");
  assert.equal(code({ state: "error", status: 500, detail: "something odd" }), null);
});

test("write tool under a read-only grant", () => {
  const active = new Set(["gw-egress", "tickets-readonly"]);
  const f = edgeFacts(helpdesk, active, "tickets-mcp:close_ticket");
  assert.deepEqual(f, { writeTool: true, readOnlyGrant: true, grantApplied: false });
  assert.equal(code({ state: "denied", status: 403, ...f }), "write-denied");
  // the read-only tool is covered by the grant: a 403 there means it hasn't taken effect yet
  const ro = edgeFacts(helpdesk, active, "tickets-mcp:list_tickets");
  assert.equal(ro.grantApplied, true);
  assert.equal(code({ state: "denied", status: 403, ...ro }), "grant-not-in-effect");
  // whole-server grant covers write tools too
  const all = edgeFacts(helpdesk, new Set(["tickets-all"]), "tickets-mcp:close_ticket");
  assert.equal(all.grantApplied, true);
  assert.equal(all.readOnlyGrant, false);
});

test("grants are per agent identity (CONTRACTS §12)", () => {
  assert.equal(edgeFacts(helpdesk, new Set(["allow-hr"]), "hr-records-agent").grantApplied, true);
  assert.equal(edgeFacts(helpdesk, new Set(["allow-hr"]), "hr-assistant/hr-records-agent").grantApplied, false);
  assert.equal(edgeFacts(helpdesk, new Set(["hr-assistant-allow-hr"]), "hr-assistant/hr-records-agent").grantApplied, true);
  assert.deepEqual(edgeFacts(null, new Set(), "kb-agent"), {});
});

test("Private Networking: read-only Payroll grant leaves update_bank_details denied", () => {
  const f = edgeFacts(privnet, new Set(["gw-egress", "payroll-readonly"]), "payroll-mcp:update_bank_details");
  assert.equal(code({ state: "denied", status: 403, ...f }), "write-denied");
});

test("gateway log entries", () => {
  const base = { id: "1", timestamp: "", gateway: "g", method: "POST", url: null, mcp_method: null, mcp_tool: null, policies: [], decided_by: null, console_url: null, summary: "DENIED · x · 403" };
  const unreg = { ...base, decision: "denied", status: 403, host: "api.example.com", edge: null, component: null, simulated: false, raw: { jsonPayload: { agentGatewayInfo: {} } } } as const;
  assert.equal(code(logFacts(unreg as never, helpdesk, new Set())), "unregistered");
  const reg = { ...unreg, host: "kb.run.app", edge: "kb-agent", component: "kb-agent", raw: { jsonPayload: { agentGatewayInfo: { agentRegistryResource: "projects/0/locations/r/agents/kb" } } } };
  assert.equal(code(logFacts(reg as never, helpdesk, new Set())), "egress-denied");
  const themeHost = { ...reg, raw: { jsonPayload: { agentGatewayInfo: {} } } }; // known theme host, resource field missing
  assert.equal(code(logFacts(themeHost as never, helpdesk, new Set())), "egress-denied");
  const sim = { ...reg, simulated: true, raw: {} };
  assert.equal(code(logFacts(sim as never, helpdesk, new Set())), "egress-denied");
  const ma = { ...reg, decision: "blocked" };
  assert.equal(code(logFacts(ma as never, helpdesk, new Set())), "model-armor");
  const ok = { ...reg, decision: "allowed", status: 200 };
  assert.equal(code(logFacts(ok as never, helpdesk, new Set())), null);
});

test("every outcome has a short why and fix", () => {
  for (const d of Object.values(DECODED)) {
    assert.ok(d.why.length > 20 && d.why.length < 200, d.code);
    assert.ok(d.fix.length > 10 && d.fix.length < 200, d.code);
  }
});
