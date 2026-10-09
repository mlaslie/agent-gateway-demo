// Types mirroring docs/CONTRACTS.md (§2, §4, §7) and agdemo_core/themes.py.

export type Mode = "live" | "demo" | "live_with_fallback";

export type EdgeStateName =
  | "direct"
  | "allowed"
  | "denied"
  | "blocked"
  | "pending"
  | "unknown"
  | "error";

export interface EdgeState {
  state: EdgeStateName;
  governed?: boolean;
  source?: "live" | "simulated" | "replayed";
  detail?: string;
  http_status?: number | null;
}

export type PolicyStatusName = "applied" | "removed" | "pending" | "pending_removal" | "error";

export interface PolicyStatus {
  applied: boolean;
  status: PolicyStatusName;
  detail?: string;
  changed_at?: string | null;
}

export interface ThemeSummary {
  id: string;
  name: string;
  description?: string;
  deployed: boolean;
}

export interface AppConfig {
  themes: ThemeSummary[];
  default_theme: string;
  default_mode: Mode;
  environment: { project_id: string; region: string; prefix: string };
  live_available: boolean;
  live_unavailable_reason?: string;
  can_admin: boolean;
  /** Gemini Enterprise app used for the "GE Demo" flow (prompts are pasted there). */
  gemini_enterprise?: { app_url: string; configured: boolean };
}

// ---- theme schema ----
export interface McpTool {
  name: string;
  description: string;
  read_only: boolean;
}
export interface McpServerSpec {
  display_name: string;
  description?: string;
  tools: McpTool[];
}
export interface Skill {
  id: string;
  name: string;
  description: string;
}
export interface A2AAgentSpec {
  display_name: string;
  description: string;
  skills: Skill[];
}
export interface Orchestrator {
  id: string;
  display_name: string;
  description?: string;
  instruction?: string;
}
export interface Component {
  id: string;
  kind: "a2a_agent" | "mcp_server";
  role_label?: string;
}
export type PolicyType =
  | "gateway_attach"
  | "a2a_allow"
  | "mcp_server_allow"
  | "mcp_tool_allow";
export interface Policy {
  id: string;
  text: string;
  type: PolicyType;
  /** `params.source`: id of an additional orchestrator this policy is for (CONTRACTS §12); absent = primary. */
  params: Record<string, unknown> & { source?: string };
  explain?: string;
  /** Measured GCP propagation times (seconds) for applying / removing this policy. */
  typical_seconds?: number | null;
  typical_remove_seconds?: number | null;
}
export interface ScenarioTest {
  id: string;
  label: string;
  prompt: string;
  probes: { edge: string }[];
  malicious?: boolean;
  /** Orchestrator that runs this test (CONTRACTS §12); absent = the primary. */
  agent?: string | null;
  /** Edge id → canned reply shown by the simulator for a successful call. */
  sample_replies?: Record<string, string>;
}
export interface Scenario {
  id: string;
  order: number;
  title: string;
  subtitle?: string;
  description?: string;
  flow: "egress" | "ingress";
  nodes: string[];
  preconditions: string[];
  policies: string[];
  tests: ScenarioTest[];
}
export interface Theme {
  id: string;
  name: string;
  description?: string;
  orchestrator: Orchestrator;
  /** Other Agent Runtime agents sharing the same gateways (CONTRACTS §12). */
  additional_orchestrators?: Orchestrator[];
  components: Component[];
  policies: Policy[];
  layout: Record<string, [number, number]>;
  mcp_servers: Record<string, McpServerSpec>;
  a2a_agents: Record<string, A2AAgentSpec>;
  scenarios: Scenario[];
}

export interface ThemeResponse {
  theme: Theme;
  nodes?: unknown[];
  edges?: unknown[];
}

export interface GatewayState {
  attached: boolean;
  status: string;
}

export interface ModelArmorState {
  enabled: boolean;
  status: string;
  detail?: string;
  typical_seconds?: number | null;
  changed_at?: string | null;
}

export interface ThemeState {
  policies: Record<string, PolicyStatus>;
  gateways: { egress: GatewayState; ingress: GatewayState };
  model_armor: ModelArmorState;
  edges: Record<string, EdgeState>;
  expected: Record<string, EdgeState>;
  live_error?: string;
}

export interface ProbeResult {
  edges: Record<string, EdgeState>;
  fallback?: string;
}

export interface RecordResult {
  saved: string;
  signature: string;
  events: number;
}

export type SseEvent =
  | { type: "status"; text: string }
  | { type: "edge"; edge: string; state: EdgeState }
  | { type: "message"; role: "agent" | "tool" | "user"; text: string }
  | { type: "fallback"; reason: string }
  | { type: "done"; edges: Record<string, EdgeState> }
  | { type: "error"; text: string };

/** POST /api/themes/{id}/verify: is the theme back at step 1? */
export interface VerifyCheck {
  id: string;
  label: string;
  ok: boolean | null;
  detail: string;
}
export interface VerifyResult {
  ok: boolean;
  checks: VerifyCheck[];
}

// ---- gateway logs (CONTRACTS §9) ----
export type GatewayDecision = "allowed" | "denied" | "blocked";

export interface GatewayLogPolicy {
  name: string;
  kind: "iap" | "model_armor" | "other";
  result: string;
}

/** One Agent Gateway request log entry (networkservices.googleapis.com/gateway_requests), normalized. */
export interface GatewayLogEntry {
  id: string;
  timestamp: string;
  gateway: string;
  decision: GatewayDecision;
  status: number | null;
  method: string | null;
  url: string | null;
  host: string | null;
  mcp_method: string | null;
  mcp_tool: string | null;
  /** Diagram edge id, or the component id when the tool is unknown, or null. */
  edge: string | null;
  component: string | null;
  policies: GatewayLogPolicy[];
  decided_by: string | null;
  summary: string;
  console_url: string | null;
  simulated: boolean;
  raw: Record<string, unknown>;
}

export interface GatewayLogsResponse {
  source: "live" | "simulated";
  filter: string;
  console_url: string | null;
  entries: GatewayLogEntry[]; // newest first
}

export interface GatewayLogsQuery {
  since?: string;
  denied_only?: boolean;
  limit?: number;
}

/** POST /api/themes/{id}/sync: what's in place in GCP right now (stuck pending states cleared). */
export interface SyncResult {
  policies: { id: string; label: string; applied: boolean; status: string; detail: string }[];
  model_armor: { enabled: boolean; status: string; detail: string };
}

// ---- Agent Registry view (CONTRACTS §10) ----
/** One member holding roles/iap.egressor on a registry entry (may call it through Agent Gateway). */
export interface RegistryAccess {
  member: string;
  member_label: string;
  member_kind: "orchestrator" | "project_agents" | "agent" | "user" | "service_account" | "group" | "domain" | "other";
  role: string;
  condition: { title: string; expression: string } | null;
  /** The theme policy that created this binding, when it matches one. */
  policy_id: string | null;
  policy_text: string | null;
  note: string | null;
}

export interface RegistrySkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

export interface RegistryTool {
  name: string;
  description: string;
  read_only: boolean;
  annotations: { readOnlyHint?: boolean; destructiveHint?: boolean; idempotentHint?: boolean; openWorldHint?: boolean };
}

export interface RegistryItem {
  /** Component id, "orchestrator", or the endpoint URL. */
  id: string;
  kind: "a2a_agent" | "mcp_server" | "endpoint" | "orchestrator";
  display_name: string;
  label?: string;
  resource: string;
  registry_id: string;
  description: string;
  url: string | null;
  protocol?: string | null;
  card?: { name: string; description: string; version: string | null; protocol_version: string | null; skills: RegistrySkill[] } | null;
  skills?: RegistrySkill[];
  tools?: RegistryTool[];
  /** null when the IAP policy couldn't be read (see access_error). */
  access: RegistryAccess[] | null;
  access_error: string | null;
  error: string | null;
}

export interface RegistryView {
  theme: string;
  source: "live" | "simulated";
  fetched_at: string;
  console_url: string | null;
  orchestrator: { display_name: string; principal: string | null };
  agents: RegistryItem[];
  mcp_servers: RegistryItem[];
  endpoints: { count: number; items: RegistryItem[] };
  error?: string;
}

// ---- Terraform export (CONTRACTS §11) ----
export interface TerraformExport {
  theme: string;
  /** File name -> content: main.tf, variables.tf, README.md. */
  files: Record<string, string>;
  /** Policy ids the export reflects (theme order). */
  applied: string[];
  model_armor: boolean;
  include_shared: boolean;
  source: "live" | "simulated";
  /** e.g. "gw-egress, allow-kb · Model Armor off" */
  state_line: string;
  /** Live unavailable in live_with_fallback: exported from the Demo-mode state instead. */
  error?: string;
}
