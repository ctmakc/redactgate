import { SignIn } from "@clerk/nextjs";

import { AuthShell } from "@/components/AuthShell";

export default function SignInPage() {
  return (
    <AuthShell
      title="Use any cloud model without sending it your clients."
      lede="Sign in to your firm's RedactGate console: run a prompt through the gateway, read the audit trail, set what gets sealed."
    >
      <SignIn />
    </AuthShell>
  );
}
