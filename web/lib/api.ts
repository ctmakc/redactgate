import "server-only";

/**
 * RedactGate admin API client.
 *
 * SERVER-ONLY. Server Components and server actions call the RedactGate admin API at
 * `REDACTGATE_API_BASE` with the `X-Admin-Token` header (`REDACTGATE_ADMIN_TOKEN`). The
 * token never reaches the browser, and every tenant-scoped call takes the team id that
 * `lib/session.ts` derived from the verified Clerk session — never one from the client.
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
  /** HMAC link in the team's append-only hash chain */
  event_hash?: string;
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

const SERVER_BASE = (
  process.env.REDACTGATE_API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8080"
).replace(/\/$/, "");

const ADMIN_TOKEN = process.env.REDACTGATE_ADMIN_TOKEN ?? "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${SERVER_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
        ...(init?.headers as Record<string, string> | undefined),
      },
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(0, `network error reaching the gateway: ${String(err)}`);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  return (await res.json()) as T;
}

// ── Endpoints (all tenant-scoped by teamId) ──────────────────────────────────

interface RawStats {
  requests: number;
  blocked: number;
  sessions?: number;
  median_latency_ms?: number | null;
  entities_by_type: Record<string, number>;
  by_provider: Record<string, number>;
}

export async function getStats(teamId: string): Promise<DashboardStats> {
  const raw = await request<RawStats>(`/admin/teams/${encodeURIComponent(teamId)}/stats`);
  const entity_breakdown: EntityTypeCount[] = Object.entries(raw.entities_by_type ?? {})
    .map(([entity_type, count]) => ({ entity_type, count }))
    .sort((a, b) => b.count - a.count);
  const provider_routing: ProviderRouting[] = Object.entries(raw.by_provider ?? {})
    .map(([provider, count]) => ({ provider, count }))
    .sort((a, b) => b.count - a.count);
  const total_redactions = entity_breakdown.reduce((s, e) => s + e.count, 0);

  let latest_benchmark: BenchmarkRow | null = null;
  try {
    latest_benchmark = (await getBenchmark())[0] ?? null;
  } catch {
    latest_benchmark = null;
  }

  return {
    total_requests: raw.requests ?? 0,
    total_redactions,
    blocked_count: raw.blocked ?? 0,
    sessions: raw.sessions ?? 0,
    median_latency_ms: raw.median_latency_ms ?? null,
    entity_breakdown,
    provider_routing,
    latest_benchmark,
  };
}

export async function getAudit(params: {
  teamId: string;
  page?: number;
  pageSize?: number;
  provider?: string;
  blocked?: boolean;
}): Promise<AuditPage> {
  const pageSize = params.pageSize ?? 50;
  const page = params.page ?? 1;
  const sp = new URLSearchParams();
  sp.set("team", params.teamId);
  sp.set("limit", String(pageSize));
  sp.set("offset", String((page - 1) * pageSize));
  if (params.provider) sp.set("provider", params.provider);
  if (params.blocked !== undefined) sp.set("blocked", String(params.blocked));
  const raw = await request<{ total: number; limit: number; offset: number; items: AuditEvent[] }>(
    `/admin/audit?${sp.toString()}`,
  );
  return { items: raw.items ?? [], total: raw.total ?? 0, page, page_size: pageSize };
}

export interface ChainCheck {
  ok: boolean;
  events: number;
  checked_at: string;
}

export function verifyChain(teamId: string): Promise<ChainCheck> {
  return request<ChainCheck>(`/admin/teams/${encodeURIComponent(teamId)}/audit/verify`);
}

export interface TeamPolicies {
  active_policy_id: string | null;
  items: Policy[];
}

export async function getTeamPolicies(teamId: string): Promise<TeamPolicies> {
  const raw = await request<TeamPolicies>(`/admin/teams/${encodeURIComponent(teamId)}/policies`);
  return { active_policy_id: raw.active_policy_id ?? null, items: raw.items ?? [] };
}

export function createTeamPolicy(teamId: string, body: PolicyCreate & { activate?: boolean }): Promise<Policy> {
  return request<Policy>(`/admin/teams/${encodeURIComponent(teamId)}/policies`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function activatePolicy(teamId: string, policyId: string): Promise<unknown> {
  return request(`/admin/teams/${encodeURIComponent(teamId)}/policy`, {
    method: "PUT",
    body: JSON.stringify({ policy_id: policyId }),
  });
}

export async function getBenchmark(): Promise<BenchmarkRow[]> {
  const raw = await request<{ items: BenchmarkRow[] }>("/admin/benchmark");
  return raw.items ?? [];
}

// ── Playground (explain view) ────────────────────────────────────────────────

export interface ExplainSpan {
  start: number;
  end: number;
  type: string;
  source: string;
  score: number;
}

export interface PlaygroundResult {
  answer: string;
  finishReason: string | null;
  usage: { prompt_tokens?: number; completion_tokens?: number } | null;
  explain: {
    session_id: string;
    provider: string;
    model: string | null;
    fallback_from: { provider: string; status: number; error: string }[];
    entity_counts: Record<string, number>;
    inputs: { text: string; spans: ExplainSpan[] }[];
    upstream_messages: { role: string; content: string }[];
    upstream_answer: string | null;
    latency_ms: number;
  };
  remaining: number | null;
}

export async function runPlayground(teamId: string, prompt: string): Promise<PlaygroundResult> {
  const url = `${SERVER_BASE}/admin/playground`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
    },
    body: JSON.stringify({ team_id: teamId, prompt }),
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    let message = text;
    try {
      const j = JSON.parse(text) as { error?: { message?: string }; detail?: string };
      message = j.error?.message ?? j.detail ?? text;
    } catch {
      /* keep raw text */
    }
    throw new ApiError(res.status, message);
  }
  const body = JSON.parse(text) as {
    choices: { message: { content: string }; finish_reason: string | null }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number };
    redactgate: PlaygroundResult["explain"];
  };
  const remaining = res.headers.get("X-Playground-Remaining");
  return {
    answer: body.choices?.[0]?.message?.content ?? "",
    finishReason: body.choices?.[0]?.finish_reason ?? null,
    usage: body.usage ?? null,
    explain: body.redactgate,
    remaining: remaining === null ? null : Number(remaining),
  };
}
