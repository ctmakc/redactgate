/** Standing integrity strip (grafted from the SEALED LEDGER direction): a single mono line
 * that continuously asserts the audit chain is intact — chain-of-custody, at a glance. */
export function IntegrityStrip({
  entries,
  head,
  verified,
}: {
  entries: number;
  head?: string | null;
  /** result of a server-side recompute of the team's hash chain; null = not checked */
  verified: boolean | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border border-rule bg-leaf px-3 py-2 font-mono text-micro text-graphite shadow-panel">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${verified === false ? "bg-vermillion" : "bg-seal"}`}
      />
      <span className={verified === false ? "text-vermillion" : "text-carbon"}>
        {verified === null ? "chain not checked" : verified ? "chain verified just now" : "chain BROKEN — an event was altered or removed"}
      </span>
      <span className="text-rule">·</span>
      <span>
        head <span className="text-carbon">{head ? `${head.slice(0, 4)}…${head.slice(-2)}` : "—"}</span>
      </span>
      <span className="text-rule">·</span>
      <span>
        <span className="tabular-nums text-carbon">{entries.toLocaleString()}</span> entries
      </span>
    </div>
  );
}
