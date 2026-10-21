import { test } from "node:test";
import assert from "node:assert/strict";
import { aboutLabel, changeKind, EFFECT_WINDOWS, effectPhase, effectWindow } from "./timing.ts";

test("policy types map to change kinds", () => {
  assert.equal(changeKind("a2a_allow"), "grant");
  assert.equal(changeKind("mcp_server_allow"), "grant");
  assert.equal(changeKind("mcp_tool_allow"), "grant");
  assert.equal(changeKind("gateway_attach"), "bind");
  assert.equal(changeKind("model_armor"), "model_armor");
  assert.equal(changeKind("enforcement"), "enforce");
});

test("measured windows: grants ~2 min (up to ~7), bind ~5 min, enforce 2–3 min, Model Armor a few minutes", () => {
  assert.deepEqual([EFFECT_WINDOWS.grant.typical, EFFECT_WINDOWS.grant.max, EFFECT_WINDOWS.grant.label], [120, 420, "about 2 min"]);
  assert.deepEqual([EFFECT_WINDOWS.bind.typical, EFFECT_WINDOWS.bind.label], [300, "about 5 min"]);
  assert.deepEqual([EFFECT_WINDOWS.enforce.typical, EFFECT_WINDOWS.enforce.max, EFFECT_WINDOWS.enforce.label], [150, 180, "about 2–3 min"]);
  assert.equal(EFFECT_WINDOWS.model_armor.label, "a few minutes");
  assert.equal(EFFECT_WINDOWS.model_armor.measured, false);
});

test("phase: countdown, then should be in effect, then late", () => {
  const w = EFFECT_WINDOWS.grant;
  assert.deepEqual(effectPhase(w, 0), { phase: "waiting", left: 120, fraction: 0 });
  assert.deepEqual(effectPhase(w, 30.4), { phase: "waiting", left: 90, fraction: 30.4 / 120 });
  assert.deepEqual(effectPhase(w, -5), { phase: "waiting", left: 120, fraction: 0 });
  assert.deepEqual(effectPhase(w, 120), { phase: "due" });
  assert.deepEqual(effectPhase(w, 419), { phase: "due" });
  assert.deepEqual(effectPhase(w, 420), { phase: "late" });
});

test("override (mock ?typical=N) shortens the window", () => {
  const w = effectWindow("grant", 10);
  assert.deepEqual([w.typical, w.max, w.label], [10, 20, "about 10 s"]);
  assert.equal(effectWindow("grant", 0), EFFECT_WINDOWS.grant);
  assert.equal(effectWindow("bind", null), EFFECT_WINDOWS.bind);
});

test("aboutLabel rounds to half minutes", () => {
  assert.equal(aboutLabel(45), "about 45 s");
  assert.equal(aboutLabel(90), "about 1½ min");
  assert.equal(aboutLabel(120), "about 2 min");
  assert.equal(aboutLabel(330), "about 5½ min");
});
