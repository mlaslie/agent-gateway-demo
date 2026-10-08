# Demo Script: IT / HR Helpdesk theme

A presenter runbook for the `helpdesk` theme. The full version takes **10–15 minutes**; the [5-minute variant](#5-minute-variant) is at the end. The `retail` theme has the same six tabs, so this script works for it too: swap the names using the table in [Retail mapping](#retail-mapping).

**The story in one line:** an agent on Agent Runtime starts out able to reach anything. Agent Gateway plus Agent Registry turn that into explicit, identity-based policy, per agent and per MCP tool, in both directions, with Model Armor screening the traffic.

---

## Pre-demo checklist

**The day before**
- [ ] `./agdemo preflight`: all rows pass.
- [ ] `./agdemo status`: the helpdesk theme is deployed and the wide-open smoke test passed.
- [ ] Open the UI and check the Theme drop-down lists **IT / HR Helpdesk** and that Live mode is available (the Mode selector isn't greyed out).
- [ ] Recordings exist for every test you plan to run (`themes/helpdesk/recordings/`). Without them, fallback shows simulated results, which is fine but less convincing.

**60 minutes before**
- [ ] Sign in to the UI as the presenter (the account in `ui.admin_access`). Other accounts can watch but can't toggle Live policies.
- [ ] Click **Reset** on the helpdesk theme and wait for every policy to show *removed*. The theme should be back to wide open.
- [ ] Run *Wide open → Probe every connection*. Every edge should be `direct` (grey/blue).

**10 minutes before (pre-apply)**

IAM and gateway changes take about 1–7 minutes to take effect, so stage the "after" state of the middle scenarios now. Then the Live calls during the demo show real results instead of `pending`.
- [ ] Set **Mode** to **Live with fallback**.
- [ ] Apply `gw-egress`, `allow-kb`, `tickets-readonly` (leave `allow-hr`, `tickets-all` and `allow-directory` off).
- [ ] Apply `gw-ingress` and `ingress-allowed-caller`.
- [ ] Leave the **Model Armor** checkbox **off** (also a gateway change, so either pre-test it now and turn it off again, or rely on fallback for that one).
- [ ] Wait until nothing shows *pending*, then click **Probe** once on the Model Armor tab to confirm green edges.

> Since the policies are pre-applied, you'll talk through scenarios 1–2 rather than toggling them live. That's covered below. If you'd rather show the toggle, use Demo mode for tabs 1–2 (instant) and switch back to Live with fallback from tab 3.

**Screen setup:** browser at 100–125% zoom, UI full screen, a second tab open on the Cloud console (Agent Registry and the gateway's page) in case someone asks "is this real?".

---

## Scenario 1: Wide open (about 2 min)

**Click:** the *Wide open* tab, then **Run test → Probe every connection**. If the gateway is pre-applied, either switch Mode to **Demo** for this tab, or say "this is how it looked an hour ago" and run it in Demo.

**Say:**
> "This is Helpdesk Agent on Agent Runtime. It discovers what it can call from Agent Registry: two A2A agents on Cloud Run and two MCP servers. Right now there's no gateway, so nothing sits between the agent and those services. It can reach the knowledge base, but also confidential HR records, delete tickets, and reset passwords."

Then run **Ask for a salary**:
> "Nothing stops it handing salary data to anyone who can chat with it."

**Audience sees:** every edge in solid grey/blue (`direct`, not governed). The salary answer appears in the transcript.

**Recovery:** if an edge shows `error` (dark red), the Cloud Run service is likely cold or down. Say "that's a cold start", rerun, or switch to Demo for this tab.

## Scenario 2: Gateway, deny all (about 2 min)

**Click:** the *Gateway: deny all* tab. Show `gw-egress` (already applied). Click **Run test → Probe every connection**.

**Say:**
> "Now we bind the agent to Agent Gateway, an egress gateway for agent traffic. One setting on the Agent Runtime engine. The default is deny: only platform endpoints like Gemini, logging and sessions are allowed. The agent still runs and still talks to its model, but every remote agent and tool is blocked."

Open **under the hood** on `gw-egress`:
> "Under the hood it's a PATCH to the engine's `agentGatewayConfig`. No code change in the agent."

**Audience sees:** every edge red (`denied`, 403 from the gateway).

**Recovery:** if edges are still grey, the attach hasn't taken effect. The edge shows `pending` with a ghost of the expected state. Say "this takes a few minutes to take effect", and fallback replays the recording.

## Scenario 3: A2A agents on Cloud Run (about 3 min)

**Click:** the *A2A agents* tab. `allow-kb` is on, `allow-hr` is off. Run **Ask a KB question**, then **Ask for a salary**.

**Say:**
> "Policy here is written against identities, not URLs. Helpdesk Agent has an Agent Identity; KB Agent and HR Records Agent are entries in Agent Registry. The rule 'Helpdesk Agent can talk to KB Agent' is an IAM grant, `iap.egressor` on that registry entry, and the gateway enforces it on every A2A call."

After the salary test:
> "Same agent, same gateway, but HR Records isn't granted, so the call gets a 403. The agent tells the user it was blocked by policy instead of making something up."

**Audience sees:** `kb-agent` edge green with an answer about the VPN; `hr-records-agent` edge red; an agent message saying access was blocked.

**Optional live toggle:** apply `allow-hr` to show it goes *pending*, then explain propagation time. Remove it again before moving on.

## Scenario 4: MCP tools (about 3 min)

**Click:** the *MCP tools* tab. `tickets-readonly` on; `tickets-all` and `allow-directory` off. Run **Read a ticket**, then **Delete a ticket**, then **Probe every tool**.

**Say:**
> "MCP servers get finer-grained control. The gateway understands MCP, so it sees each `tools/call` and which tool is being called. The tools declare `readOnlyHint` or `destructiveHint`, and this policy allows only the read-only ones. The agent can read ticket INC-1042 but not delete it, and the Directory server isn't allowed at all, so no password resets."

**Audience sees:** on the Tickets node, the `list_tickets` and `get_ticket` rows are green and the `close_ticket` and `delete_ticket` rows are red; both Directory rows are red.

**Recovery:** if the write tools show green, per-tool enforcement hasn't taken effect (or this environment uses the split read/write endpoint fallback). Use fallback, or switch to Demo for this tab.

## Scenario 5: Users → agent (about 2 min)

**Click:** the *Users → agent* tab. `gw-ingress` and `ingress-allowed-caller` are on. Run **Call as allowed user**, then **Call as denied user**.

**Say:**
> "Same gateway product, other direction. The ingress gateway sits in front of the agent. The denied caller has `aiplatform.user`, so before the ingress gateway it could call the agent directly. Now the gateway decides who gets in, and only the allowed principal does."

**Audience sees:** `ingress:allowed` green with an answer; `ingress:denied` red (403).

## Scenario 6: Model Armor (about 2 min)

**Click:** the *Model Armor* tab. Run **Normal request** (green). Tick the **Model Armor** checkbox in the header, then run **Prompt injection + PII**.

**Say:**
> "Policy says *who* can talk to *whom*. Model Armor looks at *what* is being said. With it on, the gateway screens traffic with a Model Armor template. This prompt tries a jailbreak and includes an SSN. It's blocked even on a path policy allows."

**Audience sees:** edges that were green turn orange with a shield (`blocked`); a shield badge appears on the gateway node.

**Recovery:** turning Model Armor on is also a gateway change and can be `pending`. Live with fallback replays the recording. Mention that the checkbox applies to every scenario and every theme.

## Wrap-up (about 1 min)

> "To recap: one agent on Agent Runtime. Agent Gateway enforces egress and ingress, Agent Registry is the source of identities and endpoints, IAM is the policy language, per agent and per MCP tool, and Model Armor inspects content. None of the agents or MCP servers changed. Everything you saw is configuration."

Optionally switch the **Theme** to *Retail Store Operations* to show it's a pluggable use case. Then click **Reset** after the session.

---

## 5-minute variant

Pre-apply everything as in the checklist, and use **Live with fallback**.

| Time | Tab | Do | Say |
|---|---|---|---|
| 0:00 | Wide open (Demo mode) | Probe every connection | "No gateway, so the agent can reach everything, including HR records and delete." |
| 1:00 | A2A agents (Live with fallback) | Ask a KB question, then Ask for a salary | "Gateway attached, so default deny. Identity-based grant in Agent Registry: KB yes, HR no." |
| 2:15 | MCP tools | Probe every tool | "Per-tool control over MCP: read-only tools allowed, delete and password reset blocked." |
| 3:30 | Model Armor | Tick Model Armor, then Prompt injection + PII | "Content screening on allowed paths too." |
| 4:30 | | Wrap-up line | "No agent code changed. It's all gateway, registry and IAM configuration." |

Skip ingress, or mention it in one sentence: "The same gateway does ingress: who may call the agent."

---

## Variant: drive it from Gemini Enterprise

Use this when the audience knows Gemini Enterprise. It needs `./agdemo publish-ge <theme>` once (see SETUP.md §7).
1. Open Gemini Enterprise next to the demo UI (header link "Open Gemini Enterprise ↗") and select "Helpdesk Agent (Agent Gateway demo)".
2. Tick **GE Demo** in the UI. Clicking a test copies its prompt; paste it into Gemini Enterprise.
3. Run scenarios 1–4 and 6 as usual. Change policies in the demo UI, ask again in Gemini Enterprise, and the agent's answer changes from the real data to "blocked by policy". Click **Show what happened** to light up the diagram.
4. Talk track: "Same agent, same Gemini Enterprise experience. The difference is the gateway policy, set centrally and enforced on every outbound call."
5. Skip scenario 5 in Gemini Enterprise: it supports the egress gateway only.

## Resetting between runs

**Reset** returns the theme to step 1: removes every policy, detaches both gateways in one change, turns Model Armor off, opens the Wide open tab and clears the log. Policies that weren't applied are reported as "not present". In Live mode the detach takes about 2.5 minutes. When nothing is pending any more, the UI **verifies** the start state automatically and logs a ✓/✗ checklist. Click **Verify** at any time to re-check.

## General recovery tips

| Problem | Do this |
|---|---|
| An edge is stuck on `pending` | Keep talking. Live with fallback replays the recording. Mention that IAM propagation takes minutes, which is why you pre-applied. |
| A Live call errors (dark red) | Switch Mode to **Demo** for the rest of that tab. It's instant and uses the same expected outcomes. |
| The LLM answer wanders | Use the **Probe** buttons instead. They're deterministic and don't use the LLM. |
| The UI is unreachable | Run `./agdemo ui local --mode demo` on your laptop. It needs no GCP. |
| The state is confused after a toggle | **Reset** the theme, then re-apply the policies for the tab you're on. In Live mode, allow time for propagation, or use Demo. |

## Retail mapping

| Helpdesk | Retail |
|---|---|
| Helpdesk Agent (`helpdesk-agent`) | Store Ops Agent (`store-ops-agent`) |
| KB Agent (`kb-agent`, allowed) | Merchandising Agent (`merchandising-agent`, allowed) |
| HR Records Agent (`hr-records-agent`, denied) | Pricing Agent (`pricing-agent`, denied; cost and margin data) |
| Tickets MCP: `list_tickets`, `get_ticket` / `close_ticket`, `delete_ticket` | Inventory MCP: `list_stock`, `get_item` / `adjust_stock`, `delete_item` |
| Directory MCP: `lookup_user` / `reset_password` | Orders MCP: `lookup_order` / `issue_refund` (`orders-lookup` allows only the lookup) |
| Pre-apply: `allow-kb`, `tickets-readonly` | Pre-apply: `allow-merch`, `inventory-readonly`, `orders-lookup` |
