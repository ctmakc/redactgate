/** Lightweight horizontal bar list — pure CSS, no chart dependency. */
export function BarList({
  items,
  emptyLabel = "No data yet.",
  accent = "bg-brand-500",
}: {
  items: { label: string; value: number; mono?: boolean }[];
  emptyLabel?: string;
  accent?: string;
}) {
  if (items.length === 0) {
    return <p className="py-6 text-sm text-slate-400">{emptyLabel}</p>;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => {
        const w = Math.max(2, Math.round((item.value / max) * 100));
        return (
          <li key={item.label}>
            <div className="mb-1 flex items-center justify-between text-sm">
              <span
                className={`text-slate-600 ${item.mono ? "font-mono text-[0.8rem]" : ""}`}
              >
                {item.label}
              </span>
              <span className="font-medium tabular-nums text-slate-900">
                {item.value.toLocaleString("en-US")}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${accent}`}
                style={{ width: `${w}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
