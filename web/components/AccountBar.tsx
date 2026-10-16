"use client";

import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";

/** Firm switcher (Clerk organization = one firm = one RedactGate tenant). */
export function FirmSwitcher() {
  return (
    <OrganizationSwitcher
      hidePersonal
      afterSelectOrganizationUrl="/"
      afterCreateOrganizationUrl="/playground"
      appearance={{
        elements: {
          rootBox: { width: "100%" },
          organizationSwitcherTrigger: {
            width: "100%",
            justifyContent: "space-between",
            padding: "6px 8px",
            border: "1px solid rgb(var(--c-rule))",
            borderRadius: "4px",
            background: "rgb(var(--c-leaf))",
          },
        },
      }}
    />
  );
}

export function AccountButton() {
  return <UserButton appearance={{ elements: { avatarBox: { width: 26, height: 26 } } }} />;
}
