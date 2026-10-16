import { AuditControls } from "@/components/AuditControls";
import { EntityCounts } from "@/components/EntityCounts";
import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { Pagination } from "@/components/Pagination";
import { IntegrityStrip } from "@/components/IntegrityStrip";
import { getAudit, verifyChain, type AuditPage } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { redirect } from "next/navigation";
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
  const session = await requireSession();
  if (!session.can.readAudit) redirect("/");
  const sp = await searchParams;
  const page = Math.max(1, Number(one(sp.page) ?? "1") || 1);
  const provider = one(sp.provider);
  const blockedStr = one(sp.blocked);
  const blocked =
    blockedStr === "true" ? true : blockedStr === "false" ? false : undefined;

  let data: AuditPage = { ...FALLBACK_AUDIT, page };
  let offline: string | null = null;
  let verified: boolean | null = null;
  try {
    const [d, chain] = await Promise.all([
      getAudit({ teamId: session.teamId, page, pageSize: PAGE_SIZE, provider, blocked }),
      verifyChain(session.teamId),
    ]);
    data = d;
    verified = chain.ok;
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  const baseParams = new URLSearchParams();
  if (provider) baseParams.set("provider", provider);
  if (blockedStr) baseParams.set("blocked", blockedStr);

  return (
    <div>
      <PageHeader
        title="Audit log"
        subtitle="Tamper-evident, hash-chained request ledger for this firm. Only entity type counts are recorded; raw values are never stored or shown."
      />

      <div className="mb-4">
        <IntegrityStrip entries={data.total} head={data.items[0]?.event_hash ?? null} verified={verified} />
      </div>

      {offline ? <OfflineBanner detail={offline} /> : null}

      <AuditControls />

      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead className="border-b border-rule bg-vellum">
              <tr>
                <th className="th w-5 border-l-2 border-l-seal/55 px-0" aria-label="chain" />
                <th className="th">Time</th>
                <th className="th">Route</th>
                <th className="th">Provider</th>
                <th className="th">Entity counts</th>
                <th className="th">Status</th>
                <th className="th">Tokens</th>
                <th className="th">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule/60">
              {data.items.length === 0 ? (
                <tr>
                  <td className="td text-graphite" colSpan={8}>
                    No audit events match this view.
                  </td>
                </tr>
              ) : (
                data.items.map((ev) => (
                  <tr key={ev.id} className="hover:bg-vellum/60">
                    <td
                      className="w-5 border-b border-rule/70 border-l-2 border-l-seal/55 p-0"
                      aria-hidden
                    />
                    <td className="td whitespace-nowrap text-graphite">
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
                        <span className="stamp-block">blocked</span>
                      ) : (
                        <span className="stamp-seal">sealed</span>
                      )}
                    </td>
                    <td className="td whitespace-nowrap tabular-nums text-graphite">
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
