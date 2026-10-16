import { SignUp } from "@clerk/nextjs";

import { AuthShell } from "@/components/AuthShell";

export default function SignUpPage() {
  return (
    <AuthShell
      title="Set up a console for your firm."
      lede="Create an account, name your firm, and send a synthetic client note through the gateway to Claude. You'll see exactly what the model received."
    >
      <SignUp />
    </AuthShell>
  );
}
