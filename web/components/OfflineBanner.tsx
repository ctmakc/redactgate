export function OfflineBanner({ detail }: { detail?: string }) {
  return (
    <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      <span className="font-semibold">RedactGate API unreachable.</span> Showing an
      empty shell. Set <code className="token">REDACTGATE_API_BASE</code> and{" "}
      <code className="token">REDACTGATE_ADMIN_TOKEN</code> for the admin console to
      load live data.
      {detail ? (
        <span className="ml-1 text-amber-700/80">({detail})</span>
      ) : null}
    </div>
  );
}
