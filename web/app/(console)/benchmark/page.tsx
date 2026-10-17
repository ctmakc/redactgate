import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { getBenchmark, type BenchmarkRow } from "@/lib/api";
import { FALLBACK_BENCHMARK } from "@/lib/fallback";
import { dateTime, pct } from "@/lib/format";

export const dynamic = "force-dynamic";

function ScoreCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) {
    return <span className="text-graphite">—</span>;
  }
  const tone =
    value >= 0.9
      ? "text-seal"
      : value >= 0.75
        ? "text-vermillion"
        : "text-vermillion";
  return <span className={`font-semibold tabular-nums ${tone}`}>{pct(value)}</span>;
}

export default async function BenchmarkPage() {
  let rows: BenchmarkRow[] = FALLBACK_BENCHMARK;
  let offline: string | null = null;
  try {
    rows = await getBenchmark();
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  return (
    <div>
      <PageHeader
        title="Benchmark"
        subtitle="Detection recall & precision against labeled golden sets, plus LLM-judged answer fidelity of the redacted round-trip vs. a raw call — per provider and jurisdiction pack."
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <div className="overflow-hidden rounded-xl border border-rule bg-leaf shadow-card">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead className="border-b border-rule bg-vellum">
              <tr>
                <th className="th">Provider</th>
                <th className="th">Golden set / pack</th>
                <th className="th">Recall</th>
                <th className="th">Precision</th>
                <th className="th">Answer fidelity</th>
                <th className="th">Run</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule/60">
              {rows.length === 0 ? (
                <tr>
                  <td className="td text-graphite" colSpan={6}>
                    No benchmark runs recorded yet. Trigger the eval harness to
                    populate this scorecard.
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr key={r.id} className="hover:bg-vellum/60">
                    <td className="td font-medium text-carbon">
                      {r.provider}
                    </td>
                    <td className="td">
                      <code className="token">{r.golden_set}</code>
                    </td>
                    <td className="td">
                      <ScoreCell value={r.recall} />
                    </td>
                    <td className="td">
                      <ScoreCell value={r.precision} />
                    </td>
                    <td className="td">
                      <ScoreCell value={r.answer_fidelity} />
                    </td>
                    <td className="td whitespace-nowrap text-graphite">
                      {dateTime(r.created_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <p className="mt-4 text-xs text-graphite">
        Recall = labeled entities detected · Precision = detections that were
        correct · Answer fidelity = semantic equivalence of the redacted-roundtrip
        answer to the raw answer (LLM-judge, tolerant).
      </p>
    </div>
  );
}
