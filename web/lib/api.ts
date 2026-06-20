/**
 * RedactGate admin API client.
 *
 * Server Components / route handlers read `REDACTGATE_API_BASE`; client code reads
 * the build-time public `NEXT_PUBLIC_API_BASE`. The admin token is sent as the
 * `X-Admin-Token` header and is NEVER exposed to the browser bundle (it is only read
 * from `REDACTGATE_ADMIN_TOKEN` in server contexts).
 *
 * IMPORTANT: RedactGate stores entity TYPE COUNTS only — raw detected values are never
 * persisted, returned, or rendered. Every shape below is counts/metadata only.
 */

// ── Types mirroring the backend `/admin/*` JSON contract ─────────────────────

export type PolicyMode = "tokenize" | "mask" | "hard_block";

export interface ProviderRouting {
  provider: string;
  count: number;
}

export interface EntityTypeCount {
  entity_type: string;
  count: number;
}

export interface DashboardStats {
  /** Total requests proxied through the firewall. */
  total_requests: number;
  /** Total entities redacted across all sessions. */
  total_redactions: number;
  /** Requests rejected by a hard-block policy. */
  blocked_count: number;
  /** Distinct redaction sessions opened. */
  sessions: number;
  /** Median end-to-end latency in ms (counts/metadata only). */
  median_latency_ms: number | null;
  /** Breakdown of redactions by entity TYPE (no raw values). */
  entity_breakdown: EntityTypeCount[];
  /** Request volume routed per upstream provider. */
  provider_routing: ProviderRouting[];
  /** Most recent benchmark headline score (answer-fidelity), 0..1. */
  latest_benchmark: BenchmarkRow | null;
}

export interface AuditEvent {
  id: number;
  route: string;
  provider: string;
  /** entity_type -> count. Raw values are never stored. */
  entity_counts: Record<string, number>;
  blocked: boolean;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface AuditPage {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
}

export interface Policy {
  id: string;
  name: string;
  mode: PolicyMode;
  blocked_types: string[];
  allowed_providers: string[];
  created_at: string;
}

export interface PolicyCreate {
  name: string;
  mode: PolicyMode;
  blocked_types: string[];
  allowed_providers: string[];
}

export interface BenchmarkRow {
  id: string;
  provider: string;
  golden_set: string;
  recall: number | null;
  precision: number | null;
  answer_fidelity: number | null;
  created_at: string;
}

// ── Configuration ────────────────────────────────────────────────────────────

const SERVER_BASE =
  process.env.REDACTGATE_API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8080";

const CLIENT_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/proxy";

const ADMIN_TOKEN = process.env.REDACTGATE_ADMIN_TOKEN ?? "";

function isServer(): boolean {
  return typeof window === "undefined";
}

function baseUrl(): string {
  return isServer() ? SERVER_BASE : CLIENT_BASE;
}

function headers(extra?: HeadersInit): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  // Only attach the admin token server-side so it never ships to the browser.
  if (isServer() && ADMIN_TOKEN) h["X-Admin-Token"] = ADMIN_TOKEN;
  return { ...h, ...(extra as Record<string, string> | undefined) };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${baseUrl().replace(/\/$/, "")}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: headers(init?.headers),
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(0, `network error reaching ${url}: ${String(err)}`);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  return (await res.json()) as T;
}

// ── Endpoints ────────────────────────────────────────────────────────────────

export function getStats(): Promise<DashboardStats> {
  return request<DashboardStats>("/admin/stats");
}

export function getAudit(params: {
  page?: number;
  pageSize?: number;
  q?: string;
  provider?: string;
  blocked?: boolean;
} = {}): Promise<AuditPage> {
  const sp = new URLSearchParams();
  if (params.page) sp.set("page", String(params.page));
  if (params.pageSize) sp.set("page_size", String(params.pageSize));
  if (params.q) sp.set("q", params.q);
  if (params.provider) sp.set("provider", params.provider);
  if (params.blocked !== undefined) sp.set("blocked", String(params.blocked));
  const qs = sp.toString();
  return request<AuditPage>(`/admin/audit${qs ? `?${qs}` : ""}`);
}

export function getPolicies(): Promise<Policy[]> {
  return request<Policy[]>("/admin/policies");
}

export function createPolicy(body: PolicyCreate): Promise<Policy> {
  return request<Policy>("/admin/policies", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getBenchmark(): Promise<BenchmarkRow[]> {
  return request<BenchmarkRow[]>("/admin/benchmark");
}

/** Base URL used by the in-app proxy route handler. */
export function serverApiBase(): string {
  return SERVER_BASE;
}

export function serverAdminToken(): string {
  return ADMIN_TOKEN;
}
