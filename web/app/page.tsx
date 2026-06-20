import { BarList } from "@/components/BarList";
import { IntegrityStrip } from "@/components/IntegrityStrip";
import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { RedactionLedger } from "@/components/RedactionLedger";
import { getAudit, getStats, type DashboardStats } from "@/lib/api";
import { FALLBACK_STATS } from "@/lib/fallback";
import { ms, num, pct } from "@/lib/format";

export const dynamic = "force-dynamic";

function StatTile({
  label,
  value,
  hint,
  danger,
}: {
  label: string;
  value: string;
  hint?: string;
  danger?: boolean;
}) {
  return (
    <div className="panel px-5 py-4">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${danger ? "!text-vermillion" : ""}`}>{value}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}

export default async function DashboardPage() {
  let stats: DashboardStats = FALLBACK_STATS;
  let offline: string | null = null;
  let head: string | null = null;
  try {
    stats = await getStats();
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }
  try {
    const page = await getAudit({ pageSize: 1 });
    head = (page.items[0] as { event_hash?: string } | undefined)?.event_hash ?? null;
  } catch {
    head = null;
  }

  const bench = stats.latest_benchmark;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="redaction volume · counts only · raw values never stored"
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <div className="mb-5">
        <IntegrityStrip entries={stats.total_requests} head={head} verified />
      </div>

      {/* Hero: the redaction ledger — the most characteristic first thing. */}
      <RedactionLedger items={stats.entity_breakdown} total={stats.total_redactions} />

      {/* Supporting instrument readouts. */}
      <div className="mt-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="requests proxied" value={num(stats.total_requests)} hint={`${num(stats.sessions)} sessions`} />
        <StatTile label="entities sealed" value={num(stats.total_redactions)} hint="reversibly tokenized" />
        <StatTile label="hard-blocked" value={num(stats.blocked_count)} hint="refused at the gate" danger={stats.blocked_count > 0} />
        <StatTile label="median latency" value={ms(stats.median_latency_ms)} hint="end-to-end" />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="panel p-5">
          <h2 className="eyebrow mb-4">per-provider routing</h2>
          <BarList
            items={stats.provider_routing.map((p) => ({ label: p.provider, value: p.count }))}
            emptyLabel="no requests routed yet."
          />
        </section>

        <section className="panel p-5">
          <div className="flex items-center justify-between">
            <h2 className="eyebrow">latest benchmark</h2>
            {bench ? (
              <span className="font-mono text-micro text-graphite">
                {bench.provider} · {bench.golden_set}
              </span>
            ) : null}
          </div>
          {bench ? (
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Score label="recall" value={pct(bench.recall)} />
              <Score label="precision" value={pct(bench.precision)} />
              <Score label="fidelity" value={pct(bench.answer_fidelity)} />
            </div>
          ) : (
            <p className="mt-4 font-mono text-small text-graphite">no benchmark runs yet.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function Score({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-rule bg-vellum p-3">
      <div className="stat-label">{label}</div>
      <div className="mt-1 font-sans text-h2 font-semibold tabular-nums text-carbon">{value}</div>
    </div>
  );
}
