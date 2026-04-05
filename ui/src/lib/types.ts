/** Core domain types for the Sentinel UI. */

export interface Incident {
  id: number;
  ts_start: number;
  ts_end: number | null;
  service: string;
  status: "open" | "resolved";
  severity: Severity;
  root_cause: string | null;
  summary: string | null;
  alert_count: number;
  storm_id: number | null;
  lifecycle: Lifecycle;
}

export interface Alert {
  id: number;
  ts: number;
  source: string;
  service: string;
  status: string;
  severity: Severity;
  message: string;
  details: string | null;
  insight: string | null;
  actions: string | null;
  notified: number;
  incident_id: number | null;
  is_trigger: number;
  event_id: string | null;
}

export interface IncidentNote {
  id: number;
  ts: number;
  content: string;
}

export interface TopologyService {
  depends_on?: string[];
  type?: string;
  has_incident: boolean;
  incident_severity: Severity | null;
  incident_id: number | null;
}

export interface Topology {
  services: Record<string, TopologyService>;
  shared_resources: Record<string, unknown>;
}

export interface DashboardStats {
  open_incidents: number;
  alerts_24h: number;
  active_platforms: string[];
  mode: string;
  db: Record<string, unknown>;
  ai: Record<string, unknown>;
  security: Record<string, unknown>;
  dlq_pending: number;
  sse_clients: number;
}

export interface Settings {
  mode: string;
  min_severity: string;
  dedup_ttl: number;
  cooldown: number;
  storm_window: number;
  storm_threshold: number;
  retention_days: number;
  ai_provider: string;
  ai_concurrency: number;
  platforms: Record<string, { enabled: boolean }>;
}

export interface PulseData {
  total_alerts: number;
  last_24h: number;
  frequency: string;
  deviation: string;
}

export interface SSEEvent {
  type: "alert" | "incident" | "resolution" | "stats";
  data: Record<string, unknown>;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export type Severity = "critical" | "warning" | "info" | "unknown";
export type Lifecycle = "emerging" | "active" | "stabilizing" | "resolved";
