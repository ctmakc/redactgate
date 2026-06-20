import { sumCounts } from "@/lib/format";

/** Render an entity_type -> count map as small mono pills. Counts only. */
export function EntityCounts({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts ?? {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <span className="text-xs text-graphite">none</span>;
  }
  const total = sumCounts(counts);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {entries.map(([type, n]) => (
        <span
          key={type}
          className="badge bg-vellum font-mono text-ink-2"
          title={`${n} × ${type}`}
        >
          {type}
          <span className="ml-1 text-graphite">×{n}</span>
        </span>
      ))}
      <span className="ml-1 text-xs text-graphite">({total} total)</span>
    </div>
  );
}
