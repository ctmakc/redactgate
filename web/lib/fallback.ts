/**
 * Deterministic placeholder data used ONLY when the RedactGate API is unreachable,
 * so the console renders a coherent, empty-state-aware shell instead of crashing.
 * All values are counts/metadata — never raw entity values (which are never stored).
 */

import type {
  AuditPage,
  BenchmarkRow,
  DashboardStats,
  Policy,
} from "./api";

export const FALLBACK_STATS: DashboardStats = {
  total_requests: 0,
  total_redactions: 0,
  blocked_count: 0,
  sessions: 0,
  median_latency_ms: null,
  entity_breakdown: [],
  provider_routing: [],
  latest_benchmark: null,
};

export const FALLBACK_AUDIT: AuditPage = {
  items: [],
  total: 0,
  page: 1,
  page_size: 25,
};

export const FALLBACK_POLICIES: Policy[] = [];

export const FALLBACK_BENCHMARK: BenchmarkRow[] = [];
