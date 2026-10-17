import { OrganizationList } from "@clerk/nextjs";

import { AuthShell } from "@/components/AuthShell";

export const dynamic = "force-dynamic";

/** Signed in but no active organization: pick or create the firm you act for. */
export default function FirmPage() {
  return (
    <AuthShell
      title="Which firm are you working for?"
      lede="Each firm gets its own policies, audit chain and usage. Colleagues you invite join with a role: admin, compliance officer or member."
    >
      <OrganizationList
        hidePersonal
        afterSelectOrganizationUrl="/"
        afterCreateOrganizationUrl="/playground"
      />
    </AuthShell>
  );
}
