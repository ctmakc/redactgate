import { BarList } from "@/components/BarList";
import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { getStats, type DashboardStats } from "@/lib/api";
import { FALLBACK_STATS } from "@/lib/fallback";
import { ms, num, pct } from "@/lib/format";

export const dynamic = "force-dynamic";

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint ? <div className="mt-1 text-xs text-slate-400">{hint}</div> : null}
    </div>
  );
}

export default async function DashboardPage() {
  let stats: DashboardStats = FALLBACK_STATS;
  let offline: string | null = null;
  try {
    stats = await getStats();
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  const bench = stats.latest_benchmark;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Redaction volume, entity-type breakdown, routing, and the latest benchmark — all derived from type counts. Raw values are never persisted."
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Requests proxied"
          value={num(stats.total_requests)}
          hint={`${num(stats.sessions)} redaction sessions`}
        />
        <StatCard
          label="Entities redacted"
          value={num(stats.total_redactions)}
          hint="reversibly tokenized"
        />
        <StatCard
          label="Hard-blocked"
          value={num(stats.blocked_count)}
          hint="rejected before upstream"
        />
        <StatCard
          label="Median latency"
          value={ms(stats.median_latency_ms)}
          hint="end-to-end"
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="card">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">
            Entity-type breakdown
          </h2>
          <BarList
            items={stats.entity_breakdown.map((e) => ({
              label: e.entity_type,
              value: e.count,
              mono: true,
            }))}
            emptyLabel="No entities redacted yet."
          />
        </section>

        <section className="card">
          <h2 className="mb-4 text-sm font-semibold text-slate-900">
            Per-provider routing
          </h2>
          <BarList
            items={stats.provider_routing.map((p) => ({
              label: p.provider,
              value: p.count,
            }))}
            accent="bg-emerald-500"
            emptyLabel="No requests routed yet."
          />
        </section>
      </div>

      <section className="card mt-6">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">
            Latest benchmark
          </h2>
          {bench ? (
            <span className="font-mono text-xs text-slate-400">
              {bench.provider} · {bench.golden_set}
            </span>
          ) : null}
        </div>
        {bench ? (
          <div className="mt-4 grid grid-cols-3 gap-4">
            <Score label="Recall" value={pct(bench.recall)} />
            <Score label="Precision" value={pct(bench.precision)} />
            <Score label="Answer fidelity" value={pct(bench.answer_fidelity)} />
          </div>
        ) : (
          <p className="mt-4 text-sm text-slate-400">
            No benchmark runs recorded yet.
          </p>
        )}
      </section>
    </div>
  );
}

function Score({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="stat-label">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
        {value}
      </div>
    </div>
  );
}
