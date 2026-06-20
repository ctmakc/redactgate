/** Standing integrity strip (grafted from the SEALED LEDGER direction): a single mono line
 * that continuously asserts the audit chain is intact — chain-of-custody, at a glance. */
export function IntegrityStrip({
  entries,
  head,
  verified = true,
}: {
  entries: number;
  head?: string | null;
  verified?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border border-rule bg-leaf px-3 py-2 font-mono text-micro text-graphite shadow-panel">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${verified ? "bg-seal" : "bg-vermillion"}`}
      />
      <span className="text-carbon">{verified ? "chain verified" : "chain unverified"}</span>
      <span className="text-rule">·</span>
      <span>
        head <span className="text-carbon">{head ? `${head.slice(0, 4)}…${head.slice(-2)}` : "—"}</span>
      </span>
      <span className="text-rule">·</span>
      <span>
        <span className="tabular-nums text-carbon">{entries.toLocaleString()}</span> entries
      </span>
      <span className="text-rule">·</span>
      <span>
        <span className="text-carbon">0</span> gaps
      </span>
    </div>
  );
}
