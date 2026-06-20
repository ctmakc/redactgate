import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { PolicyForm } from "@/components/PolicyForm";
import { getPolicies, type Policy } from "@/lib/api";
import { FALLBACK_POLICIES } from "@/lib/fallback";
import { dateTime } from "@/lib/format";

export const dynamic = "force-dynamic";

const MODE_STYLES: Record<string, string> = {
  tokenize: "bg-leaf text-seal",
  mask: "bg-vermillion/10 text-vermillion",
  hard_block: "bg-vermillion/10 text-vermillion",
};

function Chips({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <span className="text-xs text-graphite">{empty}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <span key={x} className="badge bg-vellum font-mono text-ink-2">
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
          <div className="overflow-hidden rounded-xl border border-rule bg-leaf shadow-card">
            <table className="w-full border-collapse">
              <thead className="border-b border-rule bg-vellum">
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Mode</th>
                  <th className="th">Blocked types</th>
                  <th className="th">Allowed providers</th>
                  <th className="th">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule/60">
                {policies.length === 0 ? (
                  <tr>
                    <td className="td text-graphite" colSpan={5}>
                      No policies yet. Create one on the right.
                    </td>
                  </tr>
                ) : (
                  policies.map((p) => (
                    <tr key={p.id} className="hover:bg-vellum/60">
                      <td className="td font-medium text-carbon">{p.name}</td>
                      <td className="td">
                        <span
                          className={`badge ${MODE_STYLES[p.mode] ?? "bg-vellum text-ink-2"}`}
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
                      <td className="td whitespace-nowrap text-graphite">
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
