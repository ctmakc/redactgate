import { AuditControls } from "@/components/AuditControls";
import { EntityCounts } from "@/components/EntityCounts";
import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { getAudit, type AuditPage } from "@/lib/api";
import { FALLBACK_AUDIT } from "@/lib/fallback";
import { dateTime, ms } from "@/lib/format";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 25;

type SearchParams = Record<string, string | string[] | undefined>;

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function AuditPageRoute({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const page = Math.max(1, Number(one(sp.page) ?? "1") || 1);
  const q = one(sp.q);
  const provider = one(sp.provider);
  const blockedStr = one(sp.blocked);
  const blocked =
    blockedStr === "true" ? true : blockedStr === "false" ? false : undefined;

  let data: AuditPage = { ...FALLBACK_AUDIT, page };
  let offline: string | null = null;
  try {
    data = await getAudit({ page, pageSize: PAGE_SIZE, q, provider, blocked });
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  const baseParams = new URLSearchParams();
  if (q) baseParams.set("q", q);
  if (provider) baseParams.set("provider", provider);
  if (blockedStr) baseParams.set("blocked", blockedStr);

  return (
    <div>
      <PageHeader
        title="Audit log"
        subtitle="Tamper-evident, hash-chained request ledger. Only entity TYPE COUNTS are recorded — raw values are never stored or shown."
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <AuditControls />

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="th">Time</th>
                <th className="th">Route</th>
                <th className="th">Provider</th>
                <th className="th">Entity counts</th>
                <th className="th">Status</th>
                <th className="th">Tokens</th>
                <th className="th">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.length === 0 ? (
                <tr>
                  <td className="td text-slate-400" colSpan={7}>
                    No audit events match this view.
                  </td>
                </tr>
              ) : (
                data.items.map((ev) => (
                  <tr key={ev.id} className="hover:bg-slate-50/60">
                    <td className="td whitespace-nowrap text-slate-500">
                      {dateTime(ev.created_at)}
                    </td>
                    <td className="td">
                      <code className="token">{ev.route}</code>
                    </td>
                    <td className="td whitespace-nowrap">{ev.provider}</td>
                    <td className="td">
                      <EntityCounts counts={ev.entity_counts} />
                    </td>
                    <td className="td">
                      {ev.blocked ? (
                        <span className="badge bg-rose-100 text-rose-700">
                          blocked
                        </span>
                      ) : (
                        <span className="badge bg-emerald-100 text-emerald-700">
                          allowed
                        </span>
                      )}
                    </td>
                    <td className="td whitespace-nowrap tabular-nums text-slate-500">
                      {ev.prompt_tokens ?? "—"} / {ev.completion_tokens ?? "—"}
                    </td>
                    <td className="td whitespace-nowrap tabular-nums">
                      {ms(ev.latency_ms)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <Pagination
        page={data.page || page}
        pageSize={data.page_size || PAGE_SIZE}
        total={data.total}
        baseQuery={baseParams.toString()}
      />
    </div>
  );
}
