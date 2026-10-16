import { AuthShell } from "@/components/AuthShell";

/** Shown when the console runs without Clerk keys: nothing else is reachable. */
export default function SetupPage() {
  return (
    <AuthShell
      title="Sign-in is not configured yet."
      lede="The console refuses to open without authentication. Add your Clerk keys and restart."
    >
      <div className="panel w-full max-w-md p-6 font-mono text-small text-carbon">
        <div className="eyebrow mb-3">web/.env.local</div>
        <pre className="overflow-x-auto whitespace-pre rounded-sm border border-rule bg-vellum p-3 text-[12.5px] leading-6">
{`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
REDACTGATE_API_BASE=http://localhost:8088
REDACTGATE_ADMIN_TOKEN=<same as ADMIN_TOKEN>`}
        </pre>
        <p className="mt-4 text-micro leading-relaxed text-graphite">
          In Clerk, enable Organizations and add a role with key{" "}
          <span className="text-carbon">compliance</span> for read-only audit access.
        </p>
      </div>
    </AuthShell>
  );
}
