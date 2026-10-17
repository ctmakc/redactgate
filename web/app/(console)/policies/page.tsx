import { redirect } from "next/navigation";

import { OfflineBanner } from "@/components/OfflineBanner";
import { PageHeader } from "@/components/PageHeader";
import { PolicyForm } from "@/components/PolicyForm";
import { getTeamPolicies, type TeamPolicies } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { requireSession } from "@/lib/session";

import { activate } from "./actions";

export const dynamic = "force-dynamic";

const MODE_STAMP: Record<string, string> = {
  tokenize: "stamp-seal",
  mask: "stamp-outline",
  hard_block: "stamp-block",
};

function Chips({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <span className="font-mono text-micro text-graphite">{empty}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <span key={x} className="token">
          {x}
        </span>
      ))}
    </div>
  );
}

export default async function PoliciesPage() {
  const session = await requireSession();
  if (!session.can.readPolicies) redirect("/");
  const canEdit = session.can.managePolicies;

  let data: TeamPolicies = { active_policy_id: null, items: [] };
  let offline: string | null = null;
  try {
    data = await getTeamPolicies(session.teamId);
  } catch (err) {
    offline = err instanceof Error ? err.message : "unknown error";
  }

  return (
    <div>
      <PageHeader
        title="Policies"
        subtitle={
          canEdit
            ? "One policy is enforced for the firm at a time: tokenize, mask or hard-block, which entity types are refused outright, and which providers may receive traffic."
            : "Read-only for your role. Only firm admins can create or switch policies."
        }
      />

      {offline ? <OfflineBanner detail={offline} /> : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className={canEdit ? "lg:col-span-2" : "lg:col-span-3"}>
          <div className="panel overflow-x-auto">
            <table className="ledger">
              <thead>
                <tr>
                  <th>name</th>
                  <th>mode</th>
                  <th>blocked types</th>
                  <th>allowed providers</th>
                  <th>created</th>
                  <th aria-label="status" />
                </tr>
              </thead>
              <tbody>
                {data.items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-graphite">
                      No policies yet.
                    </td>
                  </tr>
                ) : (
                  data.items.map((p) => {
                    const active = p.id === data.active_policy_id;
                    return (
                      <tr key={p.id}>
                        <td className="font-medium text-carbon">{p.name}</td>
                        <td>
                          <span className={MODE_STAMP[p.mode] ?? "stamp-outline"}>{p.mode}</span>
                        </td>
                        <td>
                          <Chips items={p.blocked_types} empty="none" />
                        </td>
                        <td>
                          <Chips items={p.allowed_providers} empty="any" />
                        </td>
                        <td className="whitespace-nowrap font-mono text-micro">{dateTime(p.created_at)}</td>
                        <td className="whitespace-nowrap text-right">
                          {active ? (
                            <span className="stamp-seal">enforced</span>
                          ) : canEdit ? (
                            <form action={activate}>
                              <input type="hidden" name="policy_id" value={p.id} />
                              <button type="submit" className="btn-ghost !px-2.5 !py-1 text-micro">
                                enforce
                              </button>
                            </form>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {canEdit ? (
          <div>
            <PolicyForm />
          </div>
        ) : null}
      </div>
    </div>
  );
}
