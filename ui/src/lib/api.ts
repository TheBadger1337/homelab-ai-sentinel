/**
 * API client for Sentinel backend.
 * All requests use same-origin cookies for session auth.
 * No external network calls — everything is self-contained.
 */

const BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) {
    // Session expired — redirect to login
    window.location.hash = "#/login";
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

// Auth
export function login(password: string) {
  return request<{ status: string }>("/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function logout() {
  return request<{ status: string }>("/logout", { method: "POST" });
}

export function checkSession() {
  return request<{ authenticated: boolean; reason?: string }>("/session");
}

export function setup(password: string) {
  return request<{ status: string }>("/setup", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function changePassword(currentPassword: string, newPassword: string) {
  return request<{ status: string }>("/change-password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

// Dashboard
export function getStats() {
  return request<Record<string, unknown>>("/stats");
}

// Incidents
export function getIncidents(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  service?: string;
  severity?: string;
}) {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.per_page) q.set("per_page", String(params.per_page));
  if (params?.status) q.set("status", params.status);
  if (params?.service) q.set("service", params.service);
  if (params?.severity) q.set("severity", params.severity);
  const qs = q.toString();
  return request<{
    incidents: Record<string, unknown>[];
    total: number;
    page: number;
    per_page: number;
  }>(`/incidents${qs ? `?${qs}` : ""}`);
}

export function getIncident(id: number) {
  return request<{
    incident: Record<string, unknown>;
    alerts: Record<string, unknown>[];
    notes: Record<string, unknown>[];
    topology: Record<string, unknown> | null;
    similar: Record<string, unknown>[];
  }>(`/incidents/${id}`);
}

export function resolveIncident(id: number, summary?: string) {
  return request<{ status: string }>(`/incidents/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({ summary: summary || "Manually resolved via UI" }),
  });
}

export function addNote(incidentId: number, content: string) {
  return request<{ status: string; note_id: number }>(
    `/incidents/${incidentId}/notes`,
    { method: "POST", body: JSON.stringify({ content }) }
  );
}

// Alerts
export function getAlerts(params?: {
  page?: number;
  per_page?: number;
  service?: string;
}) {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.per_page) q.set("per_page", String(params.per_page));
  if (params?.service) q.set("service", params.service);
  const qs = q.toString();
  return request<{
    alerts: Record<string, unknown>[];
    total: number;
    page: number;
    per_page: number;
  }>(`/alerts${qs ? `?${qs}` : ""}`);
}

export function getAlert(id: number) {
  return request<{ alert: Record<string, unknown> }>(`/alerts/${id}`);
}

export function deleteAlert(id: number) {
  return request<{ status: string; deleted: number }>(`/alerts/${id}`, {
    method: "DELETE",
  });
}

export function deleteAlerts(filters: {
  all?: boolean;
  service?: string;
  severity?: string;
}) {
  return request<{ status: string; deleted: number }>("/alerts/delete", {
    method: "POST",
    body: JSON.stringify(filters),
  });
}

// Feedback
export function submitFeedback(
  alertId: number,
  rating: "up" | "down" | "meh",
  comment?: string
) {
  return request<{ status: string; alert_id: number; rating: string }>(
    `/alerts/${alertId}/feedback`,
    {
      method: "POST",
      body: JSON.stringify({ rating, comment: comment || undefined }),
    }
  );
}

export function getAlertFeedback(alertId: number) {
  return request<{
    feedback: {
      id: number;
      alert_id: number;
      ts: number;
      rating: string;
      comment: string | null;
    } | null;
  }>(`/alerts/${alertId}/feedback`);
}

// Topology
export function getTopology() {
  return request<{
    services: Record<string, Record<string, unknown>>;
    shared_resources: Record<string, unknown>;
  }>("/topology");
}

// Pulse
export function getPulse(service: string) {
  return request<{ pulse: Record<string, unknown> | null }>(
    `/pulse/${encodeURIComponent(service)}`
  );
}

// Actions
export function getActions(includeRecent = false) {
  const qs = includeRecent ? "?include_recent=true" : "";
  return request<{ actions: import("./types").PendingAction[] }>(`/actions${qs}`);
}

export function approveAction(id: number) {
  return request<{ status: string; returncode: number; output: string }>(
    `/actions/${id}/approve`,
    { method: "POST" }
  );
}

export function rejectAction(id: number) {
  return request<{ status: string }>(`/actions/${id}/reject`, { method: "POST" });
}

// Settings
export function getSettings() {
  return request<Record<string, unknown>>("/settings");
}
