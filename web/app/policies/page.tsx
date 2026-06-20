import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { PolicyForm } from "@/components/PolicyForm";
import { getPolicies, type Policy } from "@/lib/api";
import { FALLBACK_POLICIES } from "@/lib/fallback";
import { dateTime } from "@/lib/format";

export const dynamic = "force-dynamic";

const MODE_STYLES: Record<string, string> = {
  tokenize: "bg-brand-50 text-brand-700",
  mask: "bg-amber-100 text-amber-700",
  hard_block: "bg-rose-100 text-rose-700",
};

function Chips({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <span className="text-xs text-slate-400">{empty}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <span key={x} className="badge bg-slate-100 font-mono text-slate-600">
          {x}
        </span>
      ))}
    </div>
  );
}

export default async function PoliciesPage() {
  let policies: Policy[] = FALLBACK_POLICIES;
  let offline: string | null = null;
  try {
    policies = await getPolicies();
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  return (
    <div>
      <PageHeader
        title="Policies"
        subtitle="Decide how each detected entity type is handled per request — tokenize, mask, or hard-block — and which upstream providers are allowed."
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
            <table className="w-full border-collapse">
              <thead className="border-b border-slate-200 bg-slate-50">
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Mode</th>
                  <th className="th">Blocked types</th>
                  <th className="th">Allowed providers</th>
                  <th className="th">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {policies.length === 0 ? (
                  <tr>
                    <td className="td text-slate-400" colSpan={5}>
                      No policies yet. Create one on the right.
                    </td>
                  </tr>
                ) : (
                  policies.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-50/60">
                      <td className="td font-medium text-slate-900">{p.name}</td>
                      <td className="td">
                        <span
                          className={`badge ${MODE_STYLES[p.mode] ?? "bg-slate-100 text-slate-600"}`}
                        >
                          {p.mode}
                        </span>
                      </td>
                      <td className="td">
                        <Chips items={p.blocked_types} empty="none" />
                      </td>
                      <td className="td">
                        <Chips items={p.allowed_providers} empty="all" />
                      </td>
                      <td className="td whitespace-nowrap text-slate-500">
                        {dateTime(p.created_at)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <PolicyForm />
        </div>
      </div>
    </div>
  );
}
