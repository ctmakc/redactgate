import { redirect } from "next/navigation";

import { PageHeader } from "@/components/PageHeader";
import { Playground } from "@/components/Playground";
import { getTeamPolicies } from "@/lib/api";
import { SAMPLES } from "@/lib/samples";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function PlaygroundPage() {
  const session = await requireSession();
  if (!session.can.runPlayground) redirect("/audit");

  let policyLabel = "the default policy";
  try {
    const p = await getTeamPolicies(session.teamId);
    const active = p.items.find((x) => x.id === p.active_policy_id);
    if (active) policyLabel = `“${active.name}” (${active.mode})`;
  } catch {
    /* label stays generic */
  }

  return (
    <div>
      <PageHeader
        title="Playground"
        subtitle="Send a client note through your firm's gateway to Claude and see each hop: what you wrote, what left the building, and what came back."
      />
      <Playground samples={SAMPLES} policyLabel={policyLabel} />
    </div>
  );
}
