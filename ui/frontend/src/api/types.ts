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
  | "mcp_tool_allow"
  | "ingress_allow";
export interface Policy {
  id: string;
  text: string;
  type: PolicyType;
  params: Record<string, unknown>;
  explain?: string;
  enforced?: boolean; // false = not enforced by GCP yet: shown as "Preview"
  note?: string;
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
  caller?: "allowed" | "denied" | null;
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
