/** Wire-format types shared between the SPA and the control plane. */

export interface StatusPayload {
  server: {
    pid: number;
    uptime_ms: number;
    policy_version: string;
    transport: string;
    audit_db_initialised: boolean;
  };
  config: {
    transport_mode: string;
    http_shared_mode_enabled: boolean;
    origin_allowlist: string[];
    redact_before_persist: boolean;
  };
  data_dir: string;
}

export interface AuditRow {
  id: string;
  timestamp: number;
  agent: string | null;
  tool: string;
  workspace_id: string | null;
  profile: string;
  approval_id: string | null;
  run_id: string;
  ok: number;
  error_code: string | null;
  exit_code: number | null;
  duration_ms: number;
  status: string;
}

export interface AuditList {
  rows: AuditRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface RuleSummary {
  id: string;
  severity: string;
  suggestion: string;
  clauses: Array<Record<string, string>>;
}

export interface RulesList {
  rules: RuleSummary[];
  hit_stats: Record<string, { rule_id: string; hit_count: number; last_hit_at: number; last_hit_cmd: string }>;
  builtin_dir: string;
  custom_dir: string | null;
}

export interface BackgroundSummary {
  id: string;
  pid: number;
  workspace_id: string;
  preset: string;
  command: string;
  cwd: string;
  port: number | null;
  started_at: number;
  status: string;
  exit_code: number | null;
  finished_at: number | null;
}

export interface McpConfigSnippet {
  codebuddy: { location: string; content: Record<string, unknown> };
  copilot: { location: string; content: Record<string, unknown> };
  http: { location: string; content: Record<string, unknown> };
}